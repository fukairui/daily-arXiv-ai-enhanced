import os
import json
import sys
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict
from queue import Queue
from threading import Lock
# INSERT_YOUR_CODE
import requests

import dotenv
import argparse
from tqdm import tqdm
import fitz  # PyMuPDF

import langchain_core.exceptions
from langchain_openai import ChatOpenAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from structure import Structure

if os.path.exists('.env'):
    dotenv.load_dotenv()
template = open("template.txt", "r").read()
system = open("system.txt", "r").read()

def extract_affiliations_from_pdf(pdf_url: str) -> str:
    """AI 回填时的兜底机构提取：历史原始 jsonl 机构为空时，重新从 PDF 前几页提取。"""
    if not pdf_url:
        return ""
    try:
        resp = requests.get(pdf_url, timeout=45)
        resp.raise_for_status()
        doc = fitz.open(stream=resp.content, filetype="pdf")
        page_texts = []
        for page in doc[:min(3, len(doc))]:
            page_texts.append(page.get_text())
        doc.close()
        text = "\n".join(page_texts)
        head = re.split(r"\b(Abstract|Introduction|1\s+Introduction)\b", text, maxsplit=1, flags=re.I)[0]
        return head.strip()[:1500]
    except Exception as e:
        print(f"Failed to backfill affiliations from {pdf_url}: {e}", file=sys.stderr)
        return ""

def ensure_affiliations(item: Dict) -> str:
    affiliations = item.get('affiliations', '') or ''
    if affiliations.strip():
        return affiliations
    pdf_url = item.get('pdf') or (f"https://arxiv.org/pdf/{item.get('id')}" if item.get('id') else '')
    affiliations = extract_affiliations_from_pdf(pdf_url)
    if affiliations:
        item['affiliations'] = affiliations
    return affiliations

KNOWN_INDUSTRY_ORGS = [
    "Google", "Google Research", "DeepMind", "OpenAI", "Microsoft", "Microsoft Research",
    "Meta", "Facebook", "Amazon", "Amazon Web Services", "AWS", "Apple", "NVIDIA",
    "Netflix", "Adobe", "Salesforce", "IBM", "Intel", "Qualcomm", "Samsung",
    "Huawei", "Noah's Ark Lab", "ByteDance", "TikTok", "Alibaba", "Ant Group",
    "Tencent", "Baidu", "JD", "Meituan", "Kuaishou", "Xiaomi", "SenseTime",
    "Shopee", "Grab", "Uber", "Airbnb", "LinkedIn", "Pinterest", "Spotify"
]

KNOWN_ACADEMIC_ORGS = [
    "MIT", "Massachusetts Institute of Technology", "Stanford University", "Carnegie Mellon University",
    "CMU", "UC Berkeley", "University of California", "UCLA", "UC San Diego", "UCSD",
    "University of Washington", "UIUC", "University of Illinois", "Cornell University",
    "Princeton University", "Harvard University", "Yale University", "Columbia University",
    "University of Toronto", "University of Montreal", "Mila", "ETH Zurich", "EPFL",
    "University of Oxford", "Oxford University", "University of Cambridge", "Cambridge University",
    "Tsinghua University", "Peking University", "Zhejiang University", "Shanghai Jiao Tong University",
    "Fudan University", "Nanjing University", "University of Science and Technology of China",
    "Chinese Academy of Sciences", "CAS", "National University of Singapore", "NUS",
    "Nanyang Technological University", "NTU", "KAIST", "Seoul National University"
]

ORG_ABBREVIATIONS = {
    "Massachusetts Institute of Technology": "MIT",
    "Carnegie Mellon University": "CMU",
    "University of California, Berkeley": "UC Berkeley",
    "University of California Berkeley": "UC Berkeley",
    "University of California, San Diego": "UCSD",
    "University of California San Diego": "UCSD",
    "University of Illinois Urbana-Champaign": "UIUC",
    "University of Illinois at Urbana-Champaign": "UIUC",
    "National University of Singapore": "NUS",
    "Nanyang Technological University": "NTU",
    "Chinese Academy of Sciences": "CAS",
    "Zhejiang University": "ZJU",
    "Shanghai Jiao Tong University": "SJTU",
    "University of Science and Technology of China": "USTC",
    "Microsoft Research": "Microsoft",
    "Google Research": "Google",
    "Amazon Web Services": "AWS",
    "Noah's Ark Lab": "Huawei",
}

def _dedupe_keep_order(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        name = (value or '').strip().strip('.,;:()[]{}')
        if not name:
            continue
        name = ORG_ABBREVIATIONS.get(name, name)
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out

def infer_affiliation_from_text(affiliations: str) -> Dict:
    """LLM 返回 unknown 时的规则兜底：只在提取文本里有较明确机构名时补全。"""
    if not affiliations or not affiliations.strip():
        return {}
    text = re.sub(r"\s+", " ", affiliations)
    lower = text.lower()

    industry = [org for org in KNOWN_INDUSTRY_ORGS if org.lower() in lower]
    academic = [org for org in KNOWN_ACADEMIC_ORGS if org.lower() in lower]

    # 捕获常见完整学术机构名，补充 known list 未覆盖的大学/研究所。
    academic_pattern = r"\b([A-Z][A-Za-z&.'\- ]{2,80}?(?:University|Institute|College|Academy|Laboratory|Lab|School))\b"
    academic.extend(re.findall(academic_pattern, text))

    industry = _dedupe_keep_order(industry)
    academic = _dedupe_keep_order(academic)
    orgs = _dedupe_keep_order(industry + academic)[:3]

    if not orgs:
        return {}

    if industry and academic:
        affiliation_type = "collaboration"
    elif industry:
        affiliation_type = "industry"
    else:
        affiliation_type = "academia"

    return {
        "affiliation_type": affiliation_type,
        "is_industrial_paper": affiliation_type in ("industry", "collaboration"),
        "org_display": ", ".join(orgs),
        "industry_orgs": ", ".join(industry),
    }

def apply_affiliation_fallback(item: Dict, affiliations: str) -> None:
    ai = item.get('AI') or {}
    needs_fallback = (
        not ai.get('org_display') or
        ai.get('affiliation_type') in ('', 'unknown', None)
    )
    if not needs_fallback:
        return
    inferred = infer_affiliation_from_text(affiliations)
    if not inferred:
        return
    for key, value in inferred.items():
        if key == 'is_industrial_paper':
            ai[key] = ai.get(key) or value
        elif not ai.get(key) or ai.get(key) == 'unknown':
            ai[key] = value
    item['AI'] = ai

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, required=True, help="jsonline data file")
    parser.add_argument("--max_workers", type=int, default=1, help="Maximum number of parallel workers")
    return parser.parse_args()

def process_single_item(chain, item: Dict, language: str) -> Dict:
    def is_sensitive(content: str) -> bool:
        """
        调用 spam.dw-dengwei.workers.dev 接口检测内容是否包含敏感词。
        返回 True 表示触发敏感词，False 表示未触发。
        """
        try:
            resp = requests.post(
                "https://spam.dw-dengwei.workers.dev",
                json={"text": content},
                timeout=5
            )
            if resp.status_code == 200:
                result = resp.json()
                # 约定接口返回 {"sensitive": true/false, ...}
                return result.get("sensitive", True)
            else:
                # 如果接口异常，默认不触发敏感词
                print(f"Sensitive check failed with status {resp.status_code}", file=sys.stderr)
                return True
        except Exception as e:
            print(f"Sensitive check error: {e}", file=sys.stderr)
            return True

    def check_github_code(content: str) -> Dict:
        """提取并验证 GitHub 链接"""
        code_info = {}

        # 1. 优先匹配 github.com/owner/repo 格式
        github_pattern = r"https?://github\.com/([a-zA-Z0-9-_]+)/([a-zA-Z0-9-_\.]+)"
        match = re.search(github_pattern, content)
        
        if match:
            owner, repo = match.groups()
            # 清理 repo 名称，去掉可能的 .git 后缀或末尾的标点
            repo = repo.rstrip(".git").rstrip(".,)")
            
            full_url = f"https://github.com/{owner}/{repo}"
            code_info["code_url"] = full_url
            
            # 尝试调用 GitHub API 获取信息
            github_token = os.environ.get("TOKEN_GITHUB")
            headers = {"Accept": "application/vnd.github.v3+json"}
            if github_token:
                headers["Authorization"] = f"token {github_token}"
            
            try:
                api_url = f"https://api.github.com/repos/{owner}/{repo}"
                resp = requests.get(api_url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    code_info["code_stars"] = data.get("stargazers_count", 0)
                    code_info["code_last_update"] = data.get("pushed_at", "")[:10]
            except Exception:
                # API 调用失败不影响主流程
                pass
            return code_info

        # 2. 如果没有 github.com，尝试匹配 github.io
        github_io_pattern = r"https?://[a-zA-Z0-9-_]+\.github\.io(?:/[a-zA-Z0-9-_\.]+)*"
        match_io = re.search(github_io_pattern, content)
        
        if match_io:
            url = match_io.group(0)
            # 清理末尾标点
            url = url.rstrip(".,)")
            code_info["code_url"] = url
            # github.io 不进行 star 和 update 判断
                
        return code_info

    # 检查 summary 字段
    if is_sensitive(item.get("summary", "")):
        return None

    # 检测代码可用性
    code_info = check_github_code(item.get("summary", ""))
    if code_info:
        item.update(code_info)

    """处理单个数据项"""
    # Default structure with meaningful fallback values
    default_ai_fields = {
        "tldr": "Summary generation failed",
        "motivation": "Motivation analysis unavailable",
        "method": "Method extraction failed",
        "result": "Result analysis unavailable",
        "conclusion": "Conclusion extraction failed",
        "is_ab_test": False,
        "is_industrial_paper": False,
        "affiliation_type": "unknown",
        "org_display": "",
        "industry_orgs": ""
    }
    
    try:
        affiliations = ensure_affiliations(item)
        response: Structure = chain.invoke({
            "language": language,
            "content": item['summary'],
            "affiliations": affiliations
        })
        item['AI'] = response.model_dump()
        # 一致性纠正:产学合作也算工业界参与
        if item['AI'].get('affiliation_type') in ('industry', 'collaboration'):
            item['AI']['is_industrial_paper'] = True
        apply_affiliation_fallback(item, affiliations)
    except langchain_core.exceptions.OutputParserException as e:
        # 尝试从错误信息中提取 JSON 字符串并修复
        error_msg = str(e)
        partial_data = {}
        
        if "Function Structure arguments:" in error_msg:
            try:
                # 提取 JSON 字符串
                json_str = error_msg.split("Function Structure arguments:", 1)[1].strip().split('are not valid JSON')[0].strip()
                # 预处理 LaTeX 数学符号 - 使用四个反斜杠来确保正确转义
                json_str = json_str.replace('\\', '\\\\')
                # 尝试解析修复后的 JSON
                partial_data = json.loads(json_str)
            except Exception as json_e:
                print(f"Failed to parse JSON for {item.get('id', 'unknown')}: {json_e}", file=sys.stderr)
        
        # Merge partial data with defaults to ensure all fields exist
        item['AI'] = {**default_ai_fields, **partial_data}
        apply_affiliation_fallback(item, item.get('affiliations', ''))
        print(f"Using partial AI data for {item.get('id', 'unknown')}: {list(partial_data.keys())}", file=sys.stderr)
    except Exception as e:
        # Catch any other exceptions and provide default values
        print(f"Unexpected error for {item.get('id', 'unknown')}: {e}", file=sys.stderr)
        item['AI'] = default_ai_fields
    
    # Final validation to ensure all required fields exist
    for field in default_ai_fields.keys():
        if field not in item['AI']:
            item['AI'][field] = default_ai_fields[field]

    # 检查 AI 生成的所有字段
    for v in item.get("AI", {}).values():
        if is_sensitive(str(v)):
            return None
    return item

def process_all_items(data: List[Dict], model_name: str, language: str, max_workers: int) -> List[Dict]:
    """并行处理所有数据项"""
    llm = ChatOpenAI(model=model_name).with_structured_output(Structure, method="function_calling")
    print('Connect to:', model_name, file=sys.stderr)
    
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system),
        HumanMessagePromptTemplate.from_template(template=template)
    ])

    chain = prompt_template | llm
    
    # 使用线程池并行处理
    processed_data = [None] * len(data)  # 预分配结果列表
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_idx = {
            executor.submit(process_single_item, chain, item, language): idx
            for idx, item in enumerate(data)
        }
        
        # 使用tqdm显示进度
        for future in tqdm(
            as_completed(future_to_idx),
            total=len(data),
            desc="Processing items"
        ):
            idx = future_to_idx[future]
            try:
                result = future.result()
                processed_data[idx] = result
            except Exception as e:
                print(f"Item at index {idx} generated an exception: {e}", file=sys.stderr)
                # Add default AI fields to ensure consistency
                processed_data[idx] = data[idx]
                processed_data[idx]['AI'] = {
                    "tldr": "Processing failed",
                    "motivation": "Processing failed",
                    "method": "Processing failed",
                    "result": "Processing failed",
                    "conclusion": "Processing failed",
                    "is_ab_test": False,
                    "is_industrial_paper": False,
                    "affiliation_type": "unknown",
                    "org_display": "",
                    "industry_orgs": ""
                }
    
    return processed_data

def main():
    args = parse_args()
    model_name = os.environ.get("MODEL_NAME", 'deepseek-chat')
    language = os.environ.get("LANGUAGE", 'Chinese')

    # 检查并删除目标文件
    target_file = args.data.replace('.jsonl', f'_AI_enhanced_{language}.jsonl')
    if os.path.exists(target_file):
        os.remove(target_file)
        print(f'Removed existing file: {target_file}', file=sys.stderr)

    # 读取数据
    data = []
    with open(args.data, "r") as f:
        for line in f:
            data.append(json.loads(line))

    # 去重
    seen_ids = set()
    unique_data = []
    for item in data:
        if item['id'] not in seen_ids:
            seen_ids.add(item['id'])
            unique_data.append(item)

    data = unique_data
    print('Open:', args.data, file=sys.stderr)
    
    # 并行处理所有数据
    processed_data = process_all_items(
        data,
        model_name,
        language,
        args.max_workers
    )
    
    # 保存结果
    with open(target_file, "w") as f:
        for item in processed_data:
            if item is not None:
                f.write(json.dumps(item) + "\n")

if __name__ == "__main__":
    main()
