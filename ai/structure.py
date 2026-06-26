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