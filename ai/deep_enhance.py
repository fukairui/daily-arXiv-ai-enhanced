import os
import sys
import json
import argparse
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


def main():
    args = parse_args()
    model_name = os.environ.get("DEEP_MODEL_NAME", "deepseek-reasoner")
    language = os.environ.get("LANGUAGE", "Chinese")

    paper_id = args.id
    pdf_url = args.pdf or f"https://arxiv.org/pdf/{paper_id}"

    print(f"Deep analyzing {paper_id} with {model_name}...", file=sys.stderr)

    full_text = download_full_text(pdf_url)
    if not full_text:
        print(f"No full text extracted for {paper_id}; aborting.", file=sys.stderr)
        sys.exit(1)

    known_names, known_tags_str = load_known_tags(args.known_tags)

    llm = ChatOpenAI(model=model_name).with_structured_output(
        DeepStructure, method="function_calling"
    )
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system),
        HumanMessagePromptTemplate.from_template(template=template),
    ])
    chain = prompt_template | llm

    response: DeepStructure = chain.invoke({
        "language": language,
        "title": args.title or paper_id,
        "affiliations": args.affiliations,
        "known_tags": known_tags_str,
        "full_text": full_text,
    })
    result = response.model_dump()

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
