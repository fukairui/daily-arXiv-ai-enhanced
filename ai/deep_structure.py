from typing import List
from pydantic import BaseModel, Field


class NewTag(BaseModel):
    name: str = Field(description="A concise English research-direction tag name, e.g. 'Semantic Identifier', 'Scaling Recommender System', 'Multi-task Modeling'")
    desc: str = Field(description="One sentence describing what this research direction is about")


class DeepStructure(BaseModel):
    """A long-form, paper-reading-level deep analysis in Markdown form, plus minimal metadata."""

    # ===== 主输出：连贯的中文 Markdown 论文精读 =====
    markdown: str = Field(
        default="",
        description=(
            "整篇论文精读以**单一中文 Markdown** 给出，要求结构清晰、逻辑连贯、技术细节扎实，"
            "可读性接近资深研究员的『论文阅读笔记』。建议章节包括：\n"
            "1. 核心问题\n2. 相关工作（按主题分组并简述代表方法）\n3. 方法概览\n4. 方法细节"
            "（按子模块拆解，可写公式与训练/推理流程；数学公式用 $...$ / $$...$$ 包裹，KaTeX 语法）\n"
            "5. 训练与部署优化\n6. 实验设置（数据/指标/对比模型/超参）\n7. 实验结果（关键表格用 GFM 表格语法）\n"
            "8. 创新点与亮点\n9. 局限性与未来工作\n10. 与相关工作的对比\n11. 评价与启示\n"
            "禁止输出 YAML front-matter、HTML 注释、代码围栏中嵌入 markdown。"
        ),
    )

    # ===== 元数据，前端列表/筛选用 =====
    summary_zh: str = Field(default="", description="一段中文 tldr 总结（1~2 句话），用于在收藏列表卡片上的『简要摘要』直接展示。")
    authors: str = Field(default="", description="作者列表（英文原文，多个作者用英文逗号分隔），从 PDF 首页提取。无法识别则留空。")
    org_display: str = Field(default="", description="本论文所有去重后的作者机构名称（英文原文，多个机构用英文逗号分隔）。无法识别则留空。")
    industry_orgs: str = Field(default="", description="本论文中的工业界 / 公司机构名称（英文原文，多个用英文逗号分隔），需是 org_display 的子集。无工业界机构则留空。")
    affiliation_type: str = Field(default="unknown", description="论文作者机构构成：industry（全部工业界）/ academia（全部学术界）/ collaboration（产学合作）/ unknown（无法识别）。仅可取这四个值。")
    is_industrial_paper: bool = Field(default=False, description="是否为工业界参与的论文。affiliation_type 为 industry 或 collaboration 时为 true。")
    is_ab_test: bool = Field(default=False, description="是否包含线上 A/B 实验。")

    # ===== 标签 =====
    tags: List[str] = Field(default_factory=list, description="从『已知标签库』中选出最贴切的 1-3 个研究方向标签（必须与已知标签名完全一致）。若没有任何已知标签贴切，可为空。")
    new_tags: List[NewTag] = Field(default_factory=list, description="仅当该论文确实属于一个全新研究方向、且已知标签库都无法覆盖时，才在此提议新标签；否则返回空列表。不要重复已知标签。")
