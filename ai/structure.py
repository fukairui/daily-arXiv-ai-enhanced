from pydantic import BaseModel, Field, model_validator

class Structure(BaseModel):
    # 给所有字段设置默认值，避免模型偶发漏掉某个字段时整条样本校验失败。
    tldr: str = Field(default="Summary generation failed", description="generate a too long; didn't read summary")
    motivation: str = Field(default="Motivation analysis unavailable", description="describe the motivation in this paper")
    method: str = Field(default="Method extraction failed", description="method of this paper")
    result: str = Field(default="Result analysis unavailable", description="result of this paper")
    conclusion: str = Field(default="Conclusion extraction failed", description="conclusion of this paper")
    is_ab_test: bool = Field(default=False, description="Whether the paper conducts A/B testing or online controlled experiments based on the abstract")
    is_industrial_paper: bool = Field(default=False, description="Whether industry is involved (true if affiliation is industry OR industry-academia collaboration)")
    affiliation_type: str = Field(default="unknown", description="Affiliation type, one of: academia, industry, collaboration, unknown")
    org_display: str = Field(default="", description="Short, comma-separated list of author affiliations using common abbreviations (e.g. 'Zhejiang University' -> 'ZJU', 'Google Research' -> 'Google'). List industry/company orgs FIRST, then academic ones. Keep at most 3 items. Empty string if unknown.")
    industry_orgs: str = Field(default="", description="Comma-separated subset of org_display that are companies/industry labs (e.g. 'Google, ByteDance'). Empty string if none.")

    @model_validator(mode="after")
    def normalize_industry_flag(self):
        if self.affiliation_type in ("industry", "collaboration"):
            self.is_industrial_paper = True
        return self
