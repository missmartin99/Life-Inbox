"""Build searchable documents from inbox items."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

TOKEN_RE = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class TextPassage:
    item_id: int
    item_type: str
    title: str
    passage: str
    source_kind: str
    preview_url: Optional[str] = None


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _split_passages(text: str, *, max_len: int = 400) -> list[str]:
    chunks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        cleaned = " ".join(paragraph.split())
        if not cleaned:
            continue
        if len(cleaned) <= max_len:
            chunks.append(cleaned)
            continue
        start = 0
        while start < len(cleaned):
            chunks.append(cleaned[start : start + max_len].strip())
            start += max_len
    return chunks


def load_passages(
    *,
    item_type: Optional[str] = None,
    processing_status: Optional[str] = None,
) -> list[TextPassage]:
    from app.database import list_items_from_db

    items = list_items_from_db(
        item_type=item_type,
        processing_status=processing_status,
        limit=None,
        offset=0,
    )
    passages: list[TextPassage] = []
    for item in items:
        if item.get("processing_status") not in {None, "ready"}:
            continue
        title = item.get("title") or item.get("original_filename") or f"Item {item['id']}"
        preview_url = None
        if item.get("item_type") == "pdf" and item.get("storage_path"):
            preview_url = f"/api/items/{item['id']}/file"
        elif item.get("item_type") == "url" and item.get("source_url"):
            preview_url = item["source_url"]

        fields: list[tuple[str, str, str]] = []
        if item.get("raw_text"):
            fields.append(("original", item["raw_text"], "original text"))
        if item.get("extracted_text") and item.get("extracted_text") != item.get(
            "raw_text"
        ):
            fields.append(("extracted", item["extracted_text"], "extracted text"))
        if not fields and item.get("raw_text"):
            fields.append(("original", item["raw_text"], "original text"))

        for kind, body, label in fields:
            for chunk in _split_passages(body):
                passages.append(
                    TextPassage(
                        item_id=item["id"],
                        item_type=item["item_type"],
                        title=title,
                        passage=chunk,
                        source_kind=label,
                        preview_url=preview_url,
                    )
                )
    return passages


def passages_to_dict(passage: TextPassage) -> dict:
    return {
        "item_id": passage.item_id,
        "item_type": passage.item_type,
        "title": passage.title,
        "passage": passage.passage,
        "source_kind": passage.source_kind,
        "preview_url": passage.preview_url,
    }
