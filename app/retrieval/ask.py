"""Grounded answers with source citations."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.organizer import extract_json_object
from app.retrieval.search import RankedPassage, search_passages

logger = logging.getLogger(__name__)

INSUFFICIENT = (
    "I do not have enough matching information in your inbox to answer that."
)

# Bound evidence packed into the LLM prompt (retrieved passages only).
_MAX_EVIDENCE_PASSAGES = 5
_MAX_PASSAGE_CHARS = 400
_MAX_EVIDENCE_TOTAL_CHARS = 6000
_MAX_ANSWER_CHARS = 2000

ASK_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")


def _citation_id(item_id: int, index: int) -> str:
    return f"item-{item_id}-{index}"


def _build_citations(ranked: list[RankedPassage]) -> list[dict]:
    citations: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for idx, row in enumerate(ranked, start=1):
        key = (row.passage.item_id, row.passage.passage[:80])
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "id": _citation_id(row.passage.item_id, idx),
                "item_id": row.passage.item_id,
                "item_type": row.passage.item_type,
                "title": row.passage.title,
                "excerpt": row.passage.passage[:320],
                "source_kind": row.passage.source_kind,
                "preview_url": row.passage.preview_url,
                "score": round(row.score, 4),
            }
        )
    return citations


def _extractive_answer(ranked: list[RankedPassage]) -> str:
    lead = ranked[0].passage
    supporting = ranked[1:3]
    parts = [
        f"Based on your inbox item “{lead.title}” ({lead.source_kind}): "
        f"{lead.passage[:280]}{'…' if len(lead.passage) > 280 else ''}"
    ]
    for row in supporting:
        parts.append(
            f"Also related: “{row.passage.title}” — {row.passage.passage[:160]}"
            f"{'…' if len(row.passage.passage) > 160 else ''}"
        )
    return " ".join(parts)


def _insufficient_payload(question: str) -> dict:
    return {
        "question": question,
        "answer": INSUFFICIENT,
        "confidence": "insufficient",
        "citations": [],
        "answer_kind": "model_generated",
        "mode": "extractive_fallback",
    }


def _extractive_payload(
    question: str,
    ranked: list[RankedPassage],
    citations: list[dict],
) -> dict:
    top = ranked[0]
    confidence = "high" if top.score >= 0.45 else "medium"
    return {
        "question": question,
        "answer": _extractive_answer(ranked),
        "confidence": confidence,
        "citations": citations,
        "answer_kind": "grounded_summary",
        "mode": "extractive_fallback",
    }


def _pack_evidence(citations: list[dict]) -> list[dict]:
    packed: list[dict] = []
    total = 0
    for cite in citations[:_MAX_EVIDENCE_PASSAGES]:
        excerpt = (cite.get("excerpt") or "")[:_MAX_PASSAGE_CHARS]
        if total + len(excerpt) > _MAX_EVIDENCE_TOTAL_CHARS:
            remaining = _MAX_EVIDENCE_TOTAL_CHARS - total
            if remaining <= 0:
                break
            excerpt = excerpt[:remaining]
        packed.append(
            {
                "citation_id": cite["id"],
                "item_id": cite["item_id"],
                "title": (cite.get("title") or "")[:120],
                "source_kind": cite.get("source_kind") or "",
                "excerpt": excerpt,
            }
        )
        total += len(excerpt)
    return packed


def _validate_llm_payload(
    parsed: dict,
    *,
    allowed_citation_ids: set[str],
    citations_by_id: dict[str, dict],
) -> Optional[dict]:
    """Return normalized answer fields or None if the model output is unusable."""
    if not isinstance(parsed, dict):
        return None

    answer = parsed.get("answer")
    if not isinstance(answer, str):
        return None
    answer = answer.strip()
    if not answer or len(answer) > _MAX_ANSWER_CHARS:
        return None

    confidence = parsed.get("confidence")
    if confidence not in {"high", "medium", "insufficient"}:
        return None

    raw_ids = parsed.get("citation_ids")
    if raw_ids is None:
        raw_ids = []
    if not isinstance(raw_ids, list):
        return None

    selected: list[dict] = []
    seen: set[str] = set()
    for cid in raw_ids:
        if not isinstance(cid, str) or cid not in allowed_citation_ids:
            return None
        if cid in seen:
            continue
        seen.add(cid)
        selected.append(citations_by_id[cid])

    if confidence == "insufficient":
        # Model may still list ids; prefer empty citations for this path.
        return {
            "answer": answer if answer else INSUFFICIENT,
            "confidence": "insufficient",
            "citations": [],
        }

    if not selected:
        return None

    return {
        "answer": answer,
        "confidence": confidence,
        "citations": selected,
    }


def _synthesize_from_evidence(
    question: str,
    citations: list[dict],
) -> Optional[dict]:
    """Call OpenAI with retrieved evidence only. Returns validated fields or None."""
    evidence = _pack_evidence(citations)
    if not evidence:
        return None

    allowed_ids = {c["id"] for c in citations}
    citations_by_id = {c["id"]: c for c in citations}

    prompt = f"""You are Ask My Inbox for a personal Life Inbox.

Answer the user's question using ONLY the supplied evidence passages from their inbox.
Do not use outside knowledge. Do not invent facts, people, dates, or citations.
If the evidence is insufficient to answer, say so clearly and set confidence to "insufficient".

Return ONLY valid JSON in exactly this shape:
{{
  "answer": "short grounded answer in plain language",
  "confidence": "high",
  "citation_ids": ["item-1-1"]
}}

Rules:
1. confidence must be one of: high, medium, insufficient.
2. citation_ids must be a subset of the citation_id values from the evidence list.
3. For factual answers (confidence high or medium), include at least one citation_id that supports the answer.
4. Never invent citation_ids that are not in the evidence.
5. Keep the answer concise and grounded in the excerpts.

Question:
{question.strip()[:500]}

Evidence (JSON):
{json.dumps(evidence, ensure_ascii=False)}
"""

    from openai import OpenAI

    client = OpenAI()
    response = client.responses.create(model=ASK_MODEL, input=prompt)
    raw = getattr(response, "output_text", None)
    if not isinstance(raw, str) or not raw.strip():
        return None
    parsed = extract_json_object(raw)
    return _validate_llm_payload(
        parsed,
        allowed_citation_ids=allowed_ids,
        citations_by_id=citations_by_id,
    )


def build_grounded_answer(
    question: str,
    *,
    limit: int = 5,
    item_type: Optional[str] = None,
) -> dict:
    ranked = search_passages(
        question,
        limit=limit,
        item_type=item_type,
        min_score=0.15,
    )
    if not ranked:
        return _insufficient_payload(question)

    top = ranked[0]
    if top.score < 0.2 and top.keyword_score < 0.25:
        return _insufficient_payload(question)

    citations = _build_citations(ranked)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        try:
            synthesized = _synthesize_from_evidence(question, citations)
            if synthesized is not None:
                if synthesized["confidence"] == "insufficient":
                    return {
                        "question": question,
                        "answer": synthesized["answer"] or INSUFFICIENT,
                        "confidence": "insufficient",
                        "citations": [],
                        "answer_kind": "model_generated",
                        "mode": "llm_grounded",
                    }
                return {
                    "question": question,
                    "answer": synthesized["answer"],
                    "confidence": synthesized["confidence"],
                    "citations": synthesized["citations"],
                    "answer_kind": "model_generated",
                    "mode": "llm_grounded",
                }
        except Exception:
            logger.exception(
                "Ask My Inbox LLM synthesis failed; using extractive fallback"
            )

    return _extractive_payload(question, ranked, citations)
