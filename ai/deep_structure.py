from typing import List
from pydantic import BaseModel, Field


class NewTag(BaseModel):
    name: str = Field(description="A concise English research-direction tag name, e.g. 'Semantic Identifier', 'Scaling Recommender System', 'Multi-task Modeling'")
    desc: str = Field(description="One sentence describing what this research direction is about")


class DeepStructure(BaseModel):
    """Rich, paper-reading-level deep analysis of a single paper based on its full text."""

    summary_zh: str = Field(default="", description="一段中文 tldr 总结（1~2 句话），用于在收藏列表卡片上的『简要摘要』直接展示。")
    authors: str = Field(default="", description="作者列表（英文原文，多个作者用英文逗号分隔），从 PDF 首页提取。无法识别则留空。")
    org_display: str = Field(default="", description="本论文所有去重后的作者机构名称（英文原文，多个机构用英文逗号分隔）。无法识别则留空。")
    industry_orgs: str = Field(default="", description="本论文中的工业界 / 公司机构名称（英文原文，多个用英文逗号分隔），需是 org_display 的子集。无工业界机构则留空。")
    affiliation_type: str = Field(default="unknown", description="论文作者机构构成：industry（全部工业界）/ academia（全部学术界）/ collaboration（产学合作）/ unknown（无法识别）。仅可取这四个值。")
    is_industrial_paper: bool = Field(default=False, description="是否为工业界参与的论文。affiliation_type 为 industry 或 collaboration 时为 true。")
    is_ab_test: bool = Field(default=False, description="是否包含线上 A/B 实验。")

    background: str = Field(description="领域背景与研究现状：该工作所处的研究领域、已有方法的局限，为读者建立上下文。要详尽，像论文 Related Work 的精炼。")
    problem: str = Field(description="本文要解决的核心问题，清晰准确地陈述。")
    motivation: str = Field(description="研究动机：为什么这个问题重要、为什么现有方法不足、作者的核心洞察。")
    method_overview: str = Field(description="方法总览：用几句话讲清整体框架与核心思想。")
    method_details: str = Field(description="方法细节：逐模块拆解关键设计、关键公式的文字解释、训练/推理流程。尽量详尽，体现论文精读深度。")
    experiments: str = Field(description="实验设置：数据集、基线、评测指标、消融设计、是否含线上 A/B 实验。")
    results_analysis: str = Field(description="实验结果与分析：关键数字、相对基线的提升、消融结论、作者得出的洞察。")
    conclusion: str = Field(description="结论：本文的最终结论与贡献总结。")
    innovations: List[str] = Field(description="创新点列表，每条一句话，突出与已有工作的区别。")
    limitations: List[str] = Field(description="局限性列表，每条一句话，指出方法/实验的不足。")
    future_work: List[str] = Field(description="未来方向列表，每条一句话。")
    related_comparison: str = Field(description="与相关工作的对比：与最接近的若干已有方法在思路/性能上的异同。")
    tags: List[str] = Field(description="从『已知标签库』中选出最贴切的 1-3 个研究方向标签（必须与已知标签名完全一致）。若没有任何已知标签贴切，可为空。")
    new_tags: List[NewTag] = Field(description="仅当该论文确实属于一个全新研究方向、且已知标签库都无法覆盖时，才在此提议新标签；否则返回空列表。不要重复已知标签。")
