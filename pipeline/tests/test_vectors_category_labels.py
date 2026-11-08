"""vectors/category_labels.json: the hand-labelled headlines the category classifier learns from."""

import json
import typing

from undercurrent import contracts
from undercurrent.vectors import categories


def test_labels_are_well_formed() -> None:
    rows = json.loads(categories.LABELS_PATH.read_text())
    valid = set(typing.get_args(contracts.Category))
    assert all(set(r) == {"title", "category"} for r in rows)
    assert {r["category"] for r in rows} <= valid
    titles = [r["title"] for r in rows]
    assert len(titles) == len(set(titles)), "a headline is labelled once"
    # every category has examples, or the classifier could never answer it
    assert {r["category"] for r in rows} | {"other"} >= valid - {"other"}
    assert len(rows) >= 200


def test_examples_cover_every_category() -> None:
    assert set(categories.CATEGORY_EXAMPLES) == set(typing.get_args(contracts.Category)) - {"other"}
