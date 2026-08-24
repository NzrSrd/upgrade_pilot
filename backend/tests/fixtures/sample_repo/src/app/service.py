"""Method calls on models: medium confidence (generic names, pydantic in scope)."""

import json

from app.models import Customer, Invoice


def serialize(invoice: Invoice) -> str:
    return json.dumps(invoice.dict())          # v2: model_dump()


def load(raw: dict) -> Customer:
    return Customer.parse_obj(raw)             # v2: model_validate()


def schema_of() -> dict:
    return Invoice.schema()                    # v2: model_json_schema()


def duplicate(invoice: Invoice) -> Invoice:
    return invoice.copy()                      # v2: model_copy()
