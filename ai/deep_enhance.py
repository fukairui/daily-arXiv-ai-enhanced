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


def update_favorites_index(data_dir: str, paper_id: str, title: str, date: str, tags, extra: dict | None = None):
    """更新/追加 data/favorites.jsonl 中该论文的索引行（按 id 去重，置 has_deep=true）。

    extra 字段会覆盖到该行：authors / org_display / industry_orgs / affiliation_type /
    is_industrial_paper / is_ab_test / summary 等，但不会清空用户已经在前端编辑过的内容
    （前端在编辑后会把对应字段写到本地 meta 与远端这条记录里；这里以远端旧行为基准，
    仅在旧值为空/未知时用新值填充）。
    """
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

    old_row = next((r for r in rows if r.get("id") == paper_id), {})
    rows = [r for r in rows if r.get("id") != paper_id]

    new_row = dict(old_row) if old_row else {}
    new_row.update({
        "id": paper_id,
        "title": title or old_row.get("title") or paper_id,
        "date": date or old_row.get("date") or "",
        "tags": tags,
        "has_deep": True,
        "favorited_at": old_row.get("favorited_at") or datetime.now(timezone.utc).isoformat(),
    })

    # 把 extra 字段以「只填充空值」的策略写入，避免覆盖用户在前端手动编辑过的内容。
    extra = extra or {}
    def _fill(field, value, allow_overwrite_unknown=False):
        if value is None:
            return
        current = new_row.get(field)
        if isinstance(value, str):
            if not value.strip():
                return
            if current and str(current).strip() and not (allow_overwrite_unknown and current == "unknown"):
                return
            new_row[field] = value.strip()
        elif isinstance(value, bool):
            # 仅在旧值缺失时填入；用户/已有 True 不要被覆盖回 False
            if field not in new_row or new_row.get(field) in (None, "", False):
                new_row[field] = value
        else:
            if current in (None, "", []):
                new_row[field] = value

    _fill("authors", extra.get("authors"))
    _fill("org_display", extra.get("org_display"))
    _fill("industry_orgs", extra.get("industry_orgs"))
    _fill("affiliation_type", extra.get("affiliation_type"), allow_overwrite_unknown=True)
    _fill("is_industrial_paper", extra.get("is_industrial_paper"))
    _fill("is_ab_test", extra.get("is_ab_test"))
    # summary 字段在前端语义是「中文 tldr / 用户笔记」，深度分析的 summary_zh 应写入这里，
    # 但若用户已手填，则不覆盖。
    _fill("summary", extra.get("summary_zh"))

    rows.append(new_row)
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
    list_fields = ["tags", "new_tags"]
    meta_text_fields = ["markdown", "summary_zh", "authors", "org_display", "industry_orgs", "affiliation_type"]
    bool_fields = ["is_industrial_paper", "is_ab_test"]

    data = dict(raw or {})
    for field in meta_text_fields:
        data[field] = str(data.get(field) or "")
    if data.get("affiliation_type") not in {"industry", "academia", "collaboration", "unknown"}:
        data["affiliation_type"] = "unknown"
    for field in bool_fields:
        data[field] = bool(data.get(field))
    # 一致性：industry / collaboration 一定算工业界参与
    if data.get("affiliation_type") in {"industry", "collaboration"}:
        data["is_industrial_paper"] = True
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
        # 新版：整篇精读用一份连贯 Markdown 表达，前端编辑器直接渲染。
        "markdown": result.get("markdown", ""),
        # 元数据快照
        "summary_zh": result.get("summary_zh", ""),
        "authors": result.get("authors", ""),
        "org_display": result.get("org_display", ""),
        "industry_orgs": result.get("industry_orgs", ""),
        "affiliation_type": result.get("affiliation_type", "unknown"),
        "is_industrial_paper": result.get("is_industrial_paper", False),
        "is_ab_test": result.get("is_ab_test", False),
    }

    data_dir = args.data_dir
    os.makedirs(os.path.join(data_dir, "deep"), exist_ok=True)
    out_path = os.path.join(data_dir, "deep", f"{paper_id}.json")
    with open(out_path, "w") as f:
        json.dump(deep_obj, f, ensure_ascii=False, indent=2)
    print(f"Wrote {out_path}", file=sys.stderr)

    # 回填 favorites.jsonl：补齐机构、产学类型、中文 tldr、A/B 实验等字段，
    # 但绝不覆盖用户已经手动维护过的 tags / summary 等。
    update_favorites_index(
        data_dir=data_dir,
        paper_id=paper_id,
        title=args.title or paper_id,
        date=args.date,
        tags=result.get("tags", []),
        extra={
            "authors": result.get("authors", ""),
            "org_display": result.get("org_display", ""),
            "industry_orgs": result.get("industry_orgs", ""),
            "affiliation_type": result.get("affiliation_type", ""),
            "is_industrial_paper": result.get("is_industrial_paper", False),
            "is_ab_test": result.get("is_ab_test", False),
            "summary_zh": result.get("summary_zh", ""),
        },
    )

    # 同时把 LLM 提议的新标签汇总到 tags_pending.json，让用户在前端确认后再合并。
    append_pending_tags(data_dir, result.get("new_tags", []), paper_id)

    # 标签现在由用户在收藏夹页面手动维护并写入 tags.json / favorites.jsonl。
    # 深度分析只产出 data/deep/{id}.json 与机构等元数据回填，不自动改写用户手动标签。


if __name__ == "__main__":
    main()
