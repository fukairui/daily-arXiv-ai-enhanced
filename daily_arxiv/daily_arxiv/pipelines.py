# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface
import arxiv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import fitz  # PyMuPDF
import requests


class DailyArxivPipeline:
    def __init__(self):
        self.page_size = 100
        self.client = arxiv.Client(self.page_size)
        # 是否下载 PDF 首页解析作者机构(默认开启,可用 FETCH_PDF=false 关闭以加速)
        self.fetch_pdf = os.environ.get("FETCH_PDF", "true").lower() != "false"

    def extract_affiliations(self, pdf_url: str) -> str:
        """下载 PDF 前几页并提取摘要前的作者机构文本。失败返回空字符串。"""
        if not self.fetch_pdf:
            return ""
        try:
            resp = requests.get(pdf_url, timeout=45)
            resp.raise_for_status()
            doc = fitz.open(stream=resp.content, filetype="pdf")
            page_texts = []
            for page in doc[:min(3, len(doc))]:
                page_texts.append(page.get_text())
            doc.close()
            # 取 Abstract/Introduction 之前的部分。部分论文首页排版复杂，Abstract 可能不在第一页。
            text = "\n".join(page_texts)
            head = re.split(r"\b(Abstract|Introduction|1\s+Introduction)\b", text, maxsplit=1, flags=re.I)[0]
            return head.strip()[:1500]
        except Exception as e:
            print(f"Failed to extract affiliations from {pdf_url}: {e}", file=sys.stderr)
            return ""
        finally:
            # 轻量节流,降低被限流概率
            time.sleep(1)

    def process_item(self, item: dict, spider):
        item["pdf"] = f"https://arxiv.org/pdf/{item['id']}"
        item["abs"] = f"https://arxiv.org/abs/{item['id']}"
        search = arxiv.Search(
            id_list=[item["id"]],
        )
        paper = next(self.client.results(search))
        item["authors"] = [a.name for a in paper.authors]
        item["title"] = paper.title
        item["categories"] = paper.categories
        item["comment"] = paper.comment
        item["summary"] = paper.summary
        item["affiliations"] = self.extract_affiliations(item["pdf"])
        return item
