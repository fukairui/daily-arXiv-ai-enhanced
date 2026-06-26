from pydantic import BaseModel, Field, field_validator
import re

class Structure(BaseModel):
    tldr: str = Field(description="generate a too long; didn't read summary")
    motivation: str = Field(description="describe the motivation in this paper")
    method: str = Field(description="method of this paper")
    result: str = Field(description="result of this paper")
    conclusion: str = Field(description="conclusion of this paper")
    is_ab_test: bool = Field(description="Whether the paper conducts A/B testing or online controlled experiments based on the abstract")
    is_industrial_paper: bool = Field(description="Whether industry is involved (true if affiliation is industry OR industry-academia collaboration)")
    affiliation_type: str = Field(description="Affiliation type, one of: academia, industry, collaboration, unknown")
    org_display: str = Field(description="Short, comma-separated list of author affiliations using common abbreviations (e.g. 'Zhejiang University' -> 'ZJU', 'Google Research' -> 'Google'). List industry/company orgs FIRST, then academic ones. Keep at most 3 items. Empty string if unknown.")
    industry_orgs: str = Field(description="Comma-separated subset of org_display that are companies/industry labs (e.g. 'Google, ByteDance'). Empty string if none.")