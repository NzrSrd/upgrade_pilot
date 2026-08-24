"""Pydantic v1 models exercising the high-confidence usage patterns."""

from typing import Optional

from pydantic import BaseModel, validator


class Customer(BaseModel):
    id: int
    email: str
    nickname: Optional[str]          # v1: implicitly optional; v2: required

    class Config:                     # v2: model_config = ConfigDict(...)
        orm_mode = True
        allow_mutation = False

    @validator("email")               # v2: @field_validator
    def email_must_contain_at(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("invalid email")
        return value


class Invoice(BaseModel):
    number: str
    customer: Customer
    note: Optional[str] = None        # explicit default: unaffected

    class Config:
        orm_mode = True
