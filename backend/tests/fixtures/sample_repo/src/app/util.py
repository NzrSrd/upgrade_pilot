"""No model-library import. `.dict()` here is a false positive if graded highly."""


class Bag:
    def __init__(self, items: dict) -> None:
        self._items = items

    def dict(self) -> dict:
        return dict(self._items)


def flatten(bag: Bag) -> dict:
    return bag.dict()          # not a model-library call
