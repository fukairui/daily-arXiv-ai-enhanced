"""单篇 arXiv 论文增强：复用 enhance.py 的 prompt + Structure（同 deepseek-chat 模型），
然后把 AI 字段回填到 data/favorites.jsonl，供前端「手动添加 arXiv」入口调用。

为什么和 enhance.py 的 process_single_item 不直接复用：
- enhance.py 的 process_single_item 会调用一个外部敏感词接口 (spam.dw-dengwei.workers.dev)，
  接口超时或非 200 时默认按"敏感"处理，整条 item 被丢弃 → 单篇增强会直接失败。
- 每日爬虫量大遇到失败丢一篇可以接受，单篇调用一次失败就一无所获。
- 因此这里只复用 template / system / Structure，自己走 chain.invoke，不做敏感词过滤。

为什么和 deep_enhance.py 分开：
- enhance.py 只读 abstract，跑 deepseek-chat（vars.MODEL_NAME），便宜、快。
- deep_enhance.py 下载 PDF 全文，跑 deepseek-reasoner（vars.DEEP_MODEL_NAME），重、贵。
手动添加 arXiv 时希望和「每日爬虫」表现一致，因此走这条轻量路径。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict

import requests

# 复用 enhance.py 中的 prompt 与 Structure
from enhance import template, system, apply_affiliation_fallback, ensure_affiliations
from structure import Structure

import langchain_core.exceptions
from langchain_openai import ChatOpenAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)


DEFAULT_AI_FIELDS = {
    "tldr": "",
    "motivation": "",
    "method": "",
    "result": "",
    "conclusion": "",
    "is_ab_test": False,
    "is_industrial_paper": False,
    "affiliation_type": "unknown",
    "org_display": "",
    "industry_orgs": "",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=str, required=True, help="arXiv paper id, e.g. 2401.12345")
    parser.add_argument("--title", type=str, default="", help="paper title")
    parser.add_argument("--abstract", type=str, default="", help="paper abstract (English)")
    parser.add_argument("--authors", type=str, default="", help="comma-separated authors")
    parser.add_argument("--categories", type=str, default="", help="comma-separated arXiv categories")
    parser.add_argument("--date", type=str, default="", help="paper date YYYY-MM-DD")
    parser.add_argument("--pdf", type=str, default="", help="PDF URL; default derived from id")
    parser.add_argument("--data-dir", type=str, default="../data", help="data directory root")
    return parser.parse_args()


def _fetch_abstract_fallback(arxiv_id: str) -> Dict[str, str]:
    """前端没传 abstract 时，作为兜底从 arXiv API 拉一下。"""
    url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        print(f"arxiv API fallback failed: {e}", file=sys.stderr)
        return {}
    out = {}
    titles = re.findall(r"<title>([\s\S]*?)</title>", text)
    if len(titles) >= 2:
        out["title"] = " ".join(titles[1].split())
    m = re.search(r"<summary>([\s\S]*?)</summary>", text)
    if m:
        out["abstract"] = " ".join(m.group(1).split())
    authors = re.findall(r"<name>([^<]+)</name>", text)
    if authors:
        out["authors"] = ", ".join(a.strip() for a in authors)
    cats = re.findall(r"<category term=\"([^\"]+)\"", text)
    if cats:
        out["categories"] = ",".join(cats)
    m = re.search(r"<published>(\d{4}-\d{2}-\d{2})", text)
    if m:
        out["date"] = m.group(1)
    return out


def _run_chain(item: Dict, language: str, model_name: str) -> Dict:
    """直接调 chain，不做敏感词检测。返回 AI 字段字典。"""
    llm = ChatOpenAI(model=model_name).with_structured_output(Structure, method="function_calling")
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system),
        HumanMessagePromptTemplate.from_template(template=template),
    ])
    chain = prompt_template | llm

    affiliations = ensure_affiliations(item)
    try:
        response: Structure = chain.invoke({
            "language": language,
            "content": item['summary'],
            "affiliations": affiliations,
        })
        ai = response.model_dump()
    except langchain_core.exceptions.OutputParserException as e:
        # 模型输出 JSON 解析失败：尽量从错误信息里救出部分字段
        partial = {}
        error_msg = str(e)
        if "Function Structure arguments:" in error_msg:
            try:
                json_str = error_msg.split("Function Structure arguments:", 1)[1].strip().split('are not valid JSON')[0].strip()
                json_str = json_str.replace('\\', '\\\\')
                partial = json.loads(json_str)
            except Exception as je:
                print(f"failed to recover partial json: {je}", file=sys.stderr)
        ai = {**DEFAULT_AI_FIELDS, **partial}
    except Exception as e:
        print(f"chain.invoke failed: {e}", file=sys.stderr)
        ai = dict(DEFAULT_AI_FIELDS)

    # 1) 先给空字段补默认，避免后续判断 None
    for k, v in DEFAULT_AI_FIELDS.items():
        if ai.get(k) in (None, ""):
            ai[k] = v

    # 2) 后验推断：如果 LLM 给出了 industry_orgs 但 affiliation_type 还是 unknown，
    #    至少能确定属于「collaboration」或「industry」；这样不会再把 affiliation_type
    #    错误地停留在 unknown，避免前端展示「未知」。
    industry_orgs_str = (ai.get("industry_orgs") or "").strip()
    org_display_str = (ai.get("org_display") or "").strip()
    if ai.get("affiliation_type") in ("", "unknown") and industry_orgs_str:
        industry_set = {o.strip().lower() for o in industry_orgs_str.split(",") if o.strip()}
        org_set = {o.strip().lower() for o in org_display_str.split(",") if o.strip()}
        if org_set and org_set <= industry_set:
            ai["affiliation_type"] = "industry"
        else:
            ai["affiliation_type"] = "collaboration"

    # 3) 一致性：industry / collaboration -> is_industrial_paper True
    if ai.get("affiliation_type") in ("industry", "collaboration"):
        ai["is_industrial_paper"] = True

    item["affiliations"] = affiliations
    item["AI"] = ai
    apply_affiliation_fallback(item, affiliations)
    return item


def _backfill_favorites(data_dir: str, paper_id: str, item: Dict):
    """把 enhance 出的 AI 字段 + 基础元数据回填到 data/favorites.jsonl。
    保留用户手动维护的 tags / summary（如果已经存在则不覆盖）。
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

    ai = item.get("AI") or {}
    new_row = dict(old_row) if old_row else {}
    new_row.update({
        "id": paper_id,
        "title": item.get("title") or old_row.get("title") or paper_id,
        "date": item.get("date") or old_row.get("date") or "",
        "abs": item.get("abs") or old_row.get("abs") or f"https://arxiv.org/abs/{paper_id}",
        "pdf": item.get("pdf") or old_row.get("pdf") or f"https://arxiv.org/pdf/{paper_id}",
        "authors": item.get("authors") or old_row.get("authors") or "",
        "categories": item.get("categories") or old_row.get("categories") or [],
        # 论文官方英文摘要 → details
        "details": item.get("summary") or old_row.get("details") or "",
        # 中文 tldr → summary（前端「简要摘要」字段）。不覆盖用户已手填内容。
        "summary": old_row.get("summary") or ai.get("tldr") or "",
        "is_ab_test": bool(ai.get("is_ab_test", old_row.get("is_ab_test", False))),
        "is_industrial_paper": bool(ai.get("is_industrial_paper", old_row.get("is_industrial_paper", False))),
        "affiliation_type": ai.get("affiliation_type") or old_row.get("affiliation_type") or "unknown",
        "org_display": ai.get("org_display") or old_row.get("org_display") or "",
        "industry_orgs": ai.get("industry_orgs") or old_row.get("industry_orgs") or "",
        "code_url": item.get("code_url") or old_row.get("code_url") or "",
        "code_stars": item.get("code_stars") or old_row.get("code_stars") or 0,
        "tags": old_row.get("tags") or [],
        "has_deep": old_row.get("has_deep", False),
        "manual_added": old_row.get("manual_added", True),
        "enhanced_at": datetime.now(timezone.utc).isoformat(),
        "favorited_at": old_row.get("favorited_at") or datetime.now(timezone.utc).isoformat(),
    })

    rows.append(new_row)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    args = parse_args()
    paper_id = args.id
    model_name = os.environ.get("MODEL_NAME", "deepseek-chat")
    language = os.environ.get("LANGUAGE", "Chinese")

    title = args.title.strip()
    abstract = args.abstract.strip()
    authors = args.authors.strip()
    categories = [c.strip() for c in args.categories.split(",") if c.strip()] if args.categories else []
    date = args.date.strip()
    pdf = args.pdf.strip() or f"https://arxiv.org/pdf/{paper_id}"
    abs_url = f"https://arxiv.org/abs/{paper_id}"

    if not (title and abstract):
        fallback = _fetch_abstract_fallback(paper_id)
        title = title or fallback.get("title", "")
        abstract = abstract or fallback.get("abstract", "")
        if not authors:
            authors = fallback.get("authors", "")
        if not categories and fallback.get("categories"):
            categories = [c.strip() for c in fallback.get("categories", "").split(",") if c.strip()]
        if not date:
            date = fallback.get("date", "")

    if not abstract:
        print(f"无法获取 abstract，放弃增强 {paper_id}", file=sys.stderr)
        sys.exit(1)

    item = {
        "id": paper_id,
        "title": title or paper_id,
        "authors": authors,
        "categories": categories,
        "abs": abs_url,
        "pdf": pdf,
        "url": abs_url,
        "summary": abstract,
        "affiliations": "",
        "date": date,
    }

    print(f"Enhancing {paper_id} with {model_name}...", file=sys.stderr)
    enhanced = _run_chain(item, language, model_name)
    _backfill_favorites(args.data_dir, paper_id, enhanced)
    print(f"Backfilled favorites.jsonl for {paper_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
