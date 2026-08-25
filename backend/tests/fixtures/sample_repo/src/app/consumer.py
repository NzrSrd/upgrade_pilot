"""Selected as a candidate (it names a model class), but its call receiver
cannot be resolved: low confidence."""

from app.models import Customer


def build(rows: list) -> list:
    return [Customer(**row) for row in rows]


def summarise(anything) -> dict:
    return anything.dict()      # receiver is unannotated: LOW, not MEDIUM
