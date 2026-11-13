"""System, tokens, webhooks, search, ask, and analysis routes."""

from __future__ import annotations

import logging
import secrets
import threading
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api_access import WEBHOOK_EVENTS, new_token
from app.capture.url_fetch import validate_public_http_url
from app.database import (
    AnalysisAlreadyRunningError,
    add_api_token,
    add_webhook,
    complete_analysis_run,
    delete_api_token,
    delete_webhook,
    dismiss_relation_in_db,
    fail_analysis_run,
    get_item_insight,
    get_latest_successful_analysis,
    get_organization_dashboard,
    list_ai_call_log,
    list_api_tokens,
    list_notes_from_db,
    list_pending_notes_from_db,
    list_relations_from_db,
    list_webhooks,
    set_item_sensitive,
    start_analysis_run,
    update_item_insight_overrides,
)
from app.organizer import analyze_inbox_with_openai, validate_analysis_result
from app.privacy import redact
from app.retrieval.ask import build_grounded_answer
from app.retrieval.search import search_passages
from app.routers._helpers import resolve
from app.schemas import (
    ApiTokenCreate,
    AskRequest,
    AskResponse,
    InboxItem,
    InsightOverrideUpdate,
    ItemInsight,
    ItemRelation,
    PersistedAnalysis,
    RedactPreviewRequest,
    SearchHit,
    SensitiveUpdate,
    WebhookCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

_analysis_lock = threading.Lock()


def _run_analysis(scope: str = "new", *, only_if_pending: bool = False):
    """Analyze notes and persist the result.

    scope "new" analyzes notes that are new or edited since their last
    analysis; "all" re-analyzes the whole inbox. With ``only_if_pending`` a
    "new" run does nothing (returns None) when there is nothing to analyze.
    """
    with _analysis_lock:
        run_id = None
        try:
            previous = None
            if scope == "new":
                previous = get_latest_successful_analysis()
                notes = list_pending_notes_from_db()
                if only_if_pending and not notes:
                    return None
                if (
                    previous is not None
                    and not notes
                    and not previous["is_stale"]
                ):
                    return {**previous, "analyzed_item_ids": []}
            else:
                notes = list_notes_from_db()

            run_id = start_analysis_run()
            analyzer = resolve("analyze_inbox_with_openai", analyze_inbox_with_openai)
            raw_result = analyzer(notes)
            result = validate_analysis_result(raw_result, notes)
            return complete_analysis_run(run_id, result, previous=previous)
        except Exception:
            if run_id is not None:
                try:
                    fail_analysis_run(run_id)
                except Exception:
                    logger.exception("Failed to mark analysis run as failed")
            raise


# --- Public API: tokens and webhooks ----------------------------------------


@router.post("/api/tokens")
def create_api_token(body: ApiTokenCreate):
    """Create an API token. The token itself is shown ONLY in this response."""
    token, token_hash = new_token()
    saved = add_api_token(body.name, token_hash)
    return {**saved, "token": token}


@router.get("/api/tokens")
def get_api_tokens():
    return list_api_tokens()


@router.delete("/api/tokens/{token_id}", status_code=204)
def revoke_api_token(token_id: int):
    if not delete_api_token(token_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return Response(status_code=204)


@router.post("/api/webhooks")
def create_webhook(body: WebhookCreate):
    """Register a URL to receive events. The signing secret is shown only once."""
    if body.event not in WEBHOOK_EVENTS:
        raise HTTPException(status_code=400, detail=f"Event must be one of {WEBHOOK_EVENTS}")
    validator = resolve("validate_public_http_url", validate_public_http_url)
    try:
        url = validator(body.url)  # blocks localhost / private IPs
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    secret = secrets.token_hex(16)
    return {**add_webhook(url, body.event, secret), "secret": secret}


@router.get("/api/webhooks")
def get_webhooks():
    return list_webhooks()


@router.delete("/api/webhooks/{webhook_id}", status_code=204)
def remove_webhook(webhook_id: int):
    if not delete_webhook(webhook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return Response(status_code=204)


# --- Privacy --------------------------------------------------------------


@router.post("/api/items/{item_id}/sensitive", response_model=InboxItem)
def mark_item_sensitive(item_id: int, body: SensitiveUpdate):
    """Mark an item sensitive (never sent to AI) or clear the flag."""
    item = set_item_sensitive(item_id, body.sensitive)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.get("/api/privacy/ai-log")
def get_ai_call_log(limit: int = Query(50, ge=1, le=200)):
    """Which AI calls were made for this user (no prompt text is stored)."""
    return list_ai_call_log(limit=limit)


@router.post("/api/privacy/redact-preview")
def redact_preview(body: RedactPreviewRequest):
    """Show what the AI would see for a piece of text, for demos and trust."""
    redacted, mapping = redact(body.text)
    return {"redacted_text": redacted, "hidden_values": len(mapping)}


@router.get("/api/items/{item_id}/insight", response_model=ItemInsight)
def read_item_insight(item_id: int):
    try:
        insight = get_item_insight(item_id)
    except Exception as exc:
        logger.exception("Failed to load item insight")
        raise HTTPException(
            status_code=500,
            detail="Unable to load insight. Please try again.",
        ) from exc
    if insight is None:
        raise HTTPException(status_code=404, detail="Insight not found.")
    return insight


@router.patch("/api/items/{item_id}/insight", response_model=ItemInsight)
def patch_item_insight(item_id: int, payload: InsightOverrideUpdate):
    try:
        insight = update_item_insight_overrides(
            item_id,
            user_title=payload.user_title,
            user_topic_id=payload.user_topic_id,
            user_priority=payload.user_priority,
            user_tags=payload.user_tags,
            clear_overrides=payload.clear_overrides,
            needs_review=payload.needs_review,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to update item insight")
        raise HTTPException(
            status_code=500,
            detail="Unable to update insight. Please try again.",
        ) from exc
    if insight is None:
        raise HTTPException(status_code=404, detail="Insight not found.")
    return insight


@router.get("/api/relations", response_model=List[ItemRelation])
def get_relations(include_dismissed: bool = Query(False)):
    try:
        return list_relations_from_db(include_dismissed=include_dismissed)
    except Exception as exc:
        logger.exception("Failed to load relations")
        raise HTTPException(
            status_code=500,
            detail="Unable to load related items. Please try again.",
        ) from exc


@router.post("/api/relations/{relation_id}/dismiss", response_model=ItemRelation)
def dismiss_relation(relation_id: int):
    try:
        relation = dismiss_relation_in_db(relation_id)
    except Exception as exc:
        logger.exception("Failed to dismiss relation")
        raise HTTPException(
            status_code=500,
            detail="Unable to dismiss relation. Please try again.",
        ) from exc
    if relation is None:
        raise HTTPException(status_code=404, detail="Relation not found.")
    return relation


@router.get("/api/search", response_model=list[SearchHit])
def search_inbox(
    q: str = Query(..., min_length=1, max_length=200),
    item_type: Optional[str] = Query(None, max_length=100),
    limit: int = Query(8, ge=1, le=25),
):
    try:
        ranked = search_passages(
            q,
            limit=limit,
            item_type=item_type,
        )
    except Exception as exc:
        logger.exception("Search failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to search inbox. Please try again.",
        ) from exc
    return [
        SearchHit(
            item_id=row.passage.item_id,
            item_type=row.passage.item_type,
            title=row.passage.title,
            excerpt=row.passage.passage[:320],
            source_kind=row.passage.source_kind,
            preview_url=row.passage.preview_url,
            score=round(row.score, 4),
            keyword_score=round(row.keyword_score, 4),
            semantic_score=round(row.semantic_score, 4),
            meaning_score=round(row.meaning_score, 4),
        )
        for row in ranked
    ]


@router.post("/api/ask", response_model=AskResponse)
def ask_my_inbox(payload: AskRequest):
    try:
        return build_grounded_answer(
            payload.question.strip(),
            item_type=payload.item_type,
        )
    except Exception as exc:
        logger.exception("Ask My Inbox failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to answer from your inbox. Please try again.",
        ) from exc


@router.get("/api/dashboard")
def organization_dashboard():
    try:
        return get_organization_dashboard()
    except Exception as exc:
        logger.exception("Failed to load organization dashboard")
        raise HTTPException(
            status_code=500,
            detail="Unable to load dashboard. Please try again.",
        ) from exc


@router.get(
    "/api/analyze/latest",
    response_model=PersistedAnalysis,
)
def get_latest_analysis():
    try:
        result = get_latest_successful_analysis()
    except Exception as exc:
        logger.exception("Failed to load latest analysis")
        raise HTTPException(
            status_code=500,
            detail="Unable to load the latest analysis. Please try again.",
        ) from exc

    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@router.get("/api/analyze/pending")
def get_pending_analysis():
    """How many notes are new or edited since their last analysis."""
    try:
        pending = list_pending_notes_from_db()
    except Exception as exc:
        logger.exception("Failed to count notes pending analysis")
        raise HTTPException(
            status_code=500,
            detail="Unable to check for new notes. Please try again.",
        ) from exc
    auto_analyzer = resolve("auto_analyzer", None)
    is_busy = auto_analyzer.busy if auto_analyzer is not None else False
    return {
        "pending_count": len(pending),
        "pending_item_ids": [note["id"] for note in pending],
        "analyzing": is_busy or _analysis_lock.locked(),
    }


@router.post("/api/analyze", response_model=PersistedAnalysis)
def analyze_inbox(scope: str = Query("new", pattern="^(new|all)$")):
    """Analyze notes that are new (or edited) since their last analysis."""
    try:
        return _run_analysis(scope)
    except AnalysisAlreadyRunningError as exc:
        raise HTTPException(
            status_code=409,
            detail="An analysis is already running. Please wait for it to finish.",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to analyze inbox")
        raise HTTPException(
            status_code=500,
            detail="Unable to analyze inbox. Please try again.",
        ) from exc
