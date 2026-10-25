"""Deterministic near-duplicate / related-item detection (no vector DB)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> str:
    return " ".join(_TOKEN_RE.findall((text or "").lower()))


def token_set(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity_score(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    seq = SequenceMatcher(None, left_norm, right_norm).ratio()
    jac = jaccard(token_set(left), token_set(right))
    return max(seq, jac)


def classify_relation(score: float) -> str | None:
    if score >= 0.92:
        return "duplicate"
    if score >= 0.78:
        return "near_duplicate"
    if score >= 0.45:
        return "related"
    return None


def find_item_relations(items: list[dict]) -> list[dict]:
    """Return undirected relation candidates for inbox items with text."""
    enriched = []
    for item in items:
        text = " ".join(
            part
            for part in (item.get("title") or "", item.get("raw_text") or "")
            if part
        ).strip()
        if text:
            enriched.append({**item, "_cmp": text})

    relations = []
    for index, left in enumerate(enriched):
        for right in enriched[index + 1 :]:
            score = similarity_score(left["_cmp"], right["_cmp"])
            relation_type = classify_relation(score)
            if not relation_type:
                continue
            a, b = sorted((left["id"], right["id"]))
            relations.append(
                {
                    "item_id_a": a,
                    "item_id_b": b,
                    "relation_type": relation_type,
                    "score": f"{score:.4f}",
                }
            )
    return relations
