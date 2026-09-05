"""Local intake metadata; deliberately separate from Finding v1."""
from typing import Literal
from pydantic import Field, field_validator, model_validator
from backend.schemas.inference import StrictSchema


class BehavioralInput(StrictSchema):
    execute: Literal[True]
    input_name: str = Field(min_length=1, max_length=128)
    output_name: str = Field(min_length=1, max_length=128)
    layout: Literal['NCHW', 'NHWC']
    value_min: float = Field(allow_inf_nan=False)
    value_max: float = Field(allow_inf_nan=False)
    classification_kind: Literal['LOGITS', 'PROBABILITIES'] | None = None
    class_axis: int = Field(ge=-8, le=7)

    @model_validator(mode='after')
    def bounds(self):
        if self.value_min >= self.value_max:
            raise ValueError('value_min must be less than value_max')
        return self


class IntakeCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(default='', max_length=128)
    notes: str = Field(default='', max_length=4096)
    behavioral: BehavioralInput | None = None

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Assessment name is required')
        return value.strip()
