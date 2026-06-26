import os
import sys
import json
import argparse
import re
from datetime import datetime, timezone

import requests
import fitz  # PyMuPDF
import dotenv

from langchain_openai import ChatOpenAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from deep_structure import DeepStructure

if os.path.exists('.env'):
    dotenv.load_dotenv()

template = open("deep_template.txt", "r").read()
system = open("deep_system.txt", "r").read()

# 全文喂给模型的字符上限（粗略控制上下文长度）
MAX_CHARS = int(os.environ.get("DEEP_MAX_CHARS", "60000"))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=str, required=True, help="arXiv paper id, e.g. 2606.26157")
    parser.add_argument("--pdf", type=str, default="", help="PDF url; default derived from id")
    parser.add_argument("--title", type=str, default="", help="paper title")
    parser.add_argument("--affiliations", type=str, default="", help="author affiliations")
    parser.add_argument("--date", type=str, default="", help="paper date (YYYY-MM-DD)")
    parser.add_argument("--known-tags", type=str, default="", help="path to tags.json (known tag library)")
    parser.add_argument("--data-dir", type=str, default="../data", help="data directory root")
    return parser.parse_args()


def download_full_text(pdf_url: str) -> str:
    """下载 PDF 并提取全文（截断到 MAX_CHARS）。失败返回空字符串。"""
    try:
        resp = requests.get(pdf_url, timeout=60)
        resp.raise_for_status()
        doc = fitz.open(stream=resp.content, filetype="pdf")
        parts = []
        for page in doc:
            parts.append(page.get_text())
        doc.close()
        text = "\n".join(parts).strip()
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
        return text
    except Exception as e:
        print(f"Failed to extract full text from {pdf_url}: {e}", file=sys.stderr)
        return ""


def load_known_tags(path: str):
    """读取 tags.json，返回 (list_of_names, formatted_string)。"""
    if not path or not os.path.exists(path):
        return [], "(none yet)"
    try:
        with open(path, "r") as f:
            data = json.load(f)
        tags = data.get("tags", [])
        names = [t.get("name", "") for t in tags if t.get("name")]
        lines = [f"- {t.get('name')}: {t.get('desc', '')}" for t in tags if t.get("name")]
        return names, ("\n".join(lines) if lines else "(none yet)")
    except Exception as e:
        print(f"Failed to load known tags from {path}: {e}", file=sys.stderr)
        return [], "(none yet)"


def append_pending_tags(data_dir: str, new_tags, paper_id: str):
    """把 LLM 提议的新标签汇总进 data/tags_pending.json（去重）。"""
    if not new_tags:
        return
    path = os.path.join(data_dir, "tags_pending.json")
    pending = {"pending": []}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                pending = json.load(f)
        except Exception:
            pending = {"pending": []}
    existing_names = {p.get("name", "").lower() for p in pending.get("pending", [])}
    for t in new_tags:
        name = t.get("name", "").strip()
        if name and name.lower() not in existing_names:
            pending["pending"].append({
                "name": name,
                "desc": t.get("desc", ""),
                "from_id": paper_id,
            })
            existing_names.add(name.lower())
    with open(path, "w") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


def update_favorites_index(data_dir: str, paper_id: str, title: str, date: str, tags):
    """更新/追加 data/favorites.jsonl 中该论文的索引行（按 id 去重，置 has_deep=true）。"""
    path = os.path.join(data_dir, "favorites.jsonl")
    rows = []
    if os.path.exists(path):
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    rows = [r for r in rows if r.get("id") != paper_id]
    rows.append({
        "id": paper_id,
        "title": title,
        "date": date,
        "tags": tags,
        "has_deep": True,
        "favorited_at": datetime.now(timezone.utc).isoformat(),
    })
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _extract_json_object(text: str) -> dict:
    """从模型输出中提取 JSON 对象。

    deepseek-reasoner 不支持 function/tool calling，因此这里用普通文本输出 JSON，
    再在本地解析。兼容 ```json ... ``` 代码块与前后带解释文本的情况。
    """
    if not text:
        raise ValueError("empty model response")

    # 优先提取 fenced json code block
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced:
        return json.loads(fenced.group(1))

    # 否则提取第一个完整 JSON 对象范围
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model response")
    return json.loads(text[start:end + 1])


def _normalize_deep_result(raw: dict) -> dict:
    """校验并规范化 deep analysis 结果，确保前端字段齐全。"""
    list_fields = ["innovations", "limitations", "future_work", "tags", "new_tags"]
    text_fields = [
        "background", "problem", "motivation", "method_overview",
        "method_details", "experiments", "results_analysis", "conclusion",
        "related_comparison",
    ]

    data = dict(raw or {})
    for field in text_fields:
        data[field] = str(data.get(field) or "")
    for field in list_fields:
        value = data.get(field)
        data[field] = value if isinstance(value, list) else []

    # new_tags 仅保留 {name, desc}
    cleaned_new_tags = []
    for t in data.get("new_tags", []):
        if isinstance(t, dict) and t.get("name"):
            cleaned_new_tags.append({"name": str(t.get("name", "")).strip(), "desc": str(t.get("desc", ""))})
    data["new_tags"] = cleaned_new_tags

    # 用 Pydantic 做最后校验，确保结构稳定
    return DeepStructure.model_validate(data).model_dump()


def main():
    args = parse_args()
    model_name = os.environ.get("DEEP_MODEL_NAME") or "deepseek-reasoner"
    language = os.environ.get("LANGUAGE") or "Chinese"

    paper_id = args.id
    pdf_url = args.pdf or f"https://arxiv.org/pdf/{paper_id}"

    print(f"Deep analyzing {paper_id} with {model_name}...", file=sys.stderr)

    full_text = download_full_text(pdf_url)
    if not full_text:
        print(f"No full text extracted for {paper_id}; aborting.", file=sys.stderr)
        sys.exit(1)

    known_names, known_tags_str = load_known_tags(args.known_tags)

    # deepseek-reasoner 是 thinking mode，不支持 tool/function calling。
    # 因此这里使用普通 chat completion，让模型输出严格 JSON，再在本地解析和 Pydantic 校验。
    llm = ChatOpenAI(model=model_name)
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system),
        HumanMessagePromptTemplate.from_template(template=template),
    ])
    chain = prompt_template | llm

    response = chain.invoke({
        "language": language,
        "title": args.title or paper_id,
        "affiliations": args.affiliations,
        "known_tags": known_tags_str,
        "full_text": full_text,
    })
    raw_json = _extract_json_object(response.content)
    result = _normalize_deep_result(raw_json)

    # 只保留确实存在于已知标签库的 tags（防止模型把新标签塞进 tags）
    if known_names:
        valid = set(known_names)
        result["tags"] = [t for t in result.get("tags", []) if t in valid]

    deep_obj = {
        "id": paper_id,
        "title": args.title or paper_id,
        "model": model_name,
        "language": language,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "tags": result.get("tags", []),
        "new_tags": result.get("new_tags", []),
        "deep": {
            k: result[k] for k in (
                "background", "problem", "motivation", "method_overview",
                "method_details", "experiments", "results_analysis",
                "conclusion", "innovations", "limitations", "future_work",
                "related_comparison",
            )
        },
    }

    data_dir = args.data_dir
    os.makedirs(os.path.join(data_dir, "deep"), exist_ok=True)
    out_path = os.path.join(data_dir, "deep", f"{paper_id}.json")
    with open(out_path, "w") as f:
        json.dump(deep_obj, f, ensure_ascii=False, indent=2)
    print(f"Wrote {out_path}", file=sys.stderr)

    append_pending_tags(data_dir, result.get("new_tags", []), paper_id)
    update_favorites_index(data_dir, paper_id, args.title or paper_id, args.date, result.get("tags", []))


if __name__ == "__main__":
    main()
