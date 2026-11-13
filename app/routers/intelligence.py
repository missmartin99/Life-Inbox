"""Smart cards, expenses, entities, flashcards, and resurfacing routes."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.attention import dates as attention_dates
from app.database import (
    add_flashcards,
    add_item_to_db,
    create_topic_in_db,
    get_entity_page,
    get_flashcard,
    get_item_from_db,
    list_due_flashcards,
    list_entities,
    list_items_by_kind,
    list_items_from_db,
    list_topics_from_db,
    save_flashcard_review,
    save_item_card,
    save_item_entities,
    update_topic_in_db,
)
from app.entities import ENTITY_TYPES, extract_entities, summarize_entity
from app.expenses import expense_summary
from app.flashcards import make_cards, next_review
from app.item_kinds import KIND_FIELDS, make_card
from app.resurface import resurface
from app.schemas import (
    FlashcardReview,
    InboxItem,
    SmartCardCreate,
    SmartCardUpdate,
    Topic,
    TopicCreate,
    TopicUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["intelligence"])


@router.get("/api/topics", response_model=List[Topic])
def get_topics(include_archived: bool = Query(False)):
    try:
        return list_topics_from_db(include_archived=include_archived)
    except Exception as exc:
        logger.exception("Failed to load topics")
        raise HTTPException(
            status_code=500,
            detail="Unable to load topics. Please try again.",
        ) from exc


@router.post("/api/topics", response_model=Topic)
def create_topic(payload: TopicCreate):
    try:
        return create_topic_in_db(payload.name.strip(), payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create topic")
        raise HTTPException(
            status_code=500,
            detail="Unable to create topic. Please try again.",
        ) from exc


@router.patch("/api/topics/{topic_id}", response_model=Topic)
def patch_topic(topic_id: int, payload: TopicUpdate):
    try:
        topic = update_topic_in_db(
            topic_id,
            name=payload.name.strip() if payload.name else None,
            description=payload.description,
            archived=payload.archived,
        )
    except Exception as exc:
        logger.exception("Failed to update topic")
        raise HTTPException(
            status_code=500,
            detail="Unable to update topic. Please try again.",
        ) from exc
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return topic


# --- Smart templates --------------------------------------------------------


@router.post("/api/items/{item_id}/card", response_model=InboxItem)
def create_item_card(item_id: int):
    """Work out what kind of item this is (recipe, book, ...) and its fields."""
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    text = item["extracted_text"] or item["raw_text"] or item["title"] or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Item has no text yet")

    card = make_card(text, is_sensitive=item["is_sensitive"])
    return save_item_card(item_id, card["kind"], card["fields"])


@router.put("/api/items/{item_id}/card", response_model=InboxItem)
def update_item_card(item_id: int, payload: SmartCardUpdate):
    """Manually update or patch a card's kind and fields."""
    if payload.kind not in KIND_FIELDS and payload.kind != "other":
        raise HTTPException(status_code=400, detail="Unknown kind")
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    updated = save_item_card(item_id, payload.kind, payload.fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return updated


@router.post("/api/cards", response_model=InboxItem)
def create_card_directly(payload: SmartCardCreate):
    """Create a new item and smart card directly."""
    if payload.kind not in KIND_FIELDS and payload.kind != "other":
        raise HTTPException(status_code=400, detail="Unknown kind")
    title = (
        payload.title
        or payload.fields.get("name")
        or payload.fields.get("title")
        or payload.fields.get("vendor")
        or f"{payload.kind.title()} Note"
    )
    raw_lines = [f"**{title}** ({payload.kind})"]
    for k, v in payload.fields.items():
        if isinstance(v, list):
            raw_lines.append(f"{k.capitalize()}: {', '.join(str(x) for x in v)}")
        else:
            raw_lines.append(f"{k.capitalize()}: {v}")
    raw_text = "\n".join(raw_lines)
    item = add_item_to_db(raw_text)
    updated = save_item_card(item["id"], payload.kind, payload.fields)
    return updated


@router.get("/api/cards", response_model=List[InboxItem])
def list_cards(kind: Optional[str] = Query(None)):
    """Items that have a smart card, optionally only one kind."""
    if kind is not None and kind not in KIND_FIELDS:
        raise HTTPException(status_code=400, detail="Unknown kind")
    return list_items_by_kind(kind)


@router.get("/api/cards/kinds")
def list_card_kinds():
    """The kinds we recognise and the fields each one has."""
    return KIND_FIELDS


# --- Expenses (receipts) ---------------------------------------------------


@router.get("/api/expenses")
def get_expenses(month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$")):
    """Totals from receipt cards, per month and per category."""
    return expense_summary(list_items_by_kind("receipt"), month=month)


# --- People & entities ------------------------------------------------------


@router.post("/api/items/{item_id}/entities")
def find_item_entities(item_id: int):
    """Find the people, places and organizations in one item and save them."""
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    text = item["extracted_text"] or item["raw_text"] or item["title"] or ""
    entities = extract_entities(text, is_sensitive=item["is_sensitive"])
    return save_item_entities(item_id, entities)


@router.get("/api/entities")
def get_entities(
    type: Optional[str] = Query(None),
    days: Optional[int] = Query(None, ge=1, le=3650),
    start_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
    end_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
):
    """All entities with how many items mention them."""
    if type is not None and type not in ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="Unknown entity type")
    return list_entities(type, days=days, start_date=start_date, end_date=end_date)


@router.get("/api/entities/{entity_id}")
def get_entity(
    entity_id: int,
    start_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
    end_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
):
    """'Everything about X': the entity and every item that mentions it."""
    page = get_entity_page(entity_id, start_date=start_date, end_date=end_date)
    if page is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return page


@router.get("/api/entities/{entity_id}/summary")
def get_entity_summary(
    entity_id: int,
    start_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
    end_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
):
    """AI-synthesized profile dossier summary for an entity from its connected items."""
    page = get_entity_page(entity_id, start_date=start_date, end_date=end_date)
    if page is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    summary = summarize_entity(
        page["name"],
        page["type"],
        page.get("items", []),
        start_date=start_date,
        end_date=end_date,
    )
    return {
        "entity_id": entity_id,
        "name": page["name"],
        "type": page["type"],
        "start_date": start_date,
        "end_date": end_date,
        "item_count": len(page.get("items", [])),
        "summary": summary,
    }


# --- Resurfacing and flashcards ---------------------------------------------


@router.get("/api/resurface")
def get_resurfaced_items():
    """Old notes worth another look: 'on this day' and a few forgotten ones."""
    today = attention_dates.today_in_configured_tz()
    return resurface(list_items_from_db(limit=None, offset=0), today)


@router.post("/api/items/{item_id}/flashcards")
def create_flashcards(item_id: int):
    """Turn an item into up to 3 flashcards, due for review today."""
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    text = item["extracted_text"] or item["raw_text"] or ""
    cards = make_cards(text, is_sensitive=item["is_sensitive"])
    if not cards:
        raise HTTPException(status_code=400, detail="Could not make cards from this item")
    today = attention_dates.today_in_configured_tz().isoformat()
    return add_flashcards(item_id, cards, due_date=today)


@router.get("/api/flashcards/due")
def get_due_flashcards():
    """Cards to review today (including overdue ones)."""
    today = attention_dates.today_in_configured_tz().isoformat()
    return list_due_flashcards(today)


@router.post("/api/flashcards/{card_id}/review")
def review_flashcard(card_id: int, body: FlashcardReview):
    """Record whether you remembered the answer; schedules the next review."""
    card = get_flashcard(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Flashcard not found")
    today = attention_dates.today_in_configured_tz()
    box, due = next_review(card["box"], body.remembered, today)
    return save_flashcard_review(card_id, box, due.isoformat())
