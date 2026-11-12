"""Voice assistant API and page: speak or type, get a spoken answer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from app.assistant import (
    SpeechError,
    classify_utterance,
    has_devanagari,
    speech_configured,
    synthesize_speech,
    to_speech,
)
from app.capture.voice_extract import transcribe_audio, validate_audio_upload
from app.database import add_item_to_db
from app.retrieval.ask import build_grounded_answer

logger = logging.getLogger(__name__)
router = APIRouter()

TEMPLATE = Path(__file__).resolve().parent / "templates" / "assistant.html"
MAX_UTTERANCE_CHARS = 2000


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


@router.get("/assistant", response_class=HTMLResponse)
def assistant_page():
    page = TEMPLATE.read_text(encoding="utf-8")
    return page.replace("__VOICE_REPLIES__", "true" if speech_configured() else "false")


def _heard(file: Optional[UploadFile], text: Optional[str]) -> str:
    """Turn the request into text (transcribing audio if given)."""
    if file is not None:
        data = file.file.read()
        try:
            filename, mime, _ = validate_audio_upload(
                file.filename or "voice.webm", file.content_type, data
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = transcribe_audio(data, original_filename=filename, mime_type=mime)
        heard = (result.get("text") or "").strip()
        if not heard:
            raise HTTPException(
                status_code=422,
                detail=result.get("note")
                or "I couldn't hear anything. Please try again.",
            )
        return heard[:MAX_UTTERANCE_CHARS]
    if text and text.strip():
        return text.strip()[:MAX_UTTERANCE_CHARS]
    raise HTTPException(status_code=400, detail="Send a recording or some text.")


@router.post("/api/assistant/voice")
def assistant_voice(
    file: Optional[UploadFile] = File(default=None),
    text: Optional[str] = Form(default=None),
):
    """Understand one spoken (or typed) request and answer it.

    "Remember to ..." / "note ..." saves to the inbox; anything else is
    answered from the user's own saved items, with sources.
    """
    transcript = _heard(file, text)
    intent, cleaned = classify_utterance(transcript)

    if intent == "capture":
        if len(cleaned) < 2:
            reply = "What would you like me to save?"
            return {
                "transcript": transcript, "intent": "clarify", "reply": reply,
                "speech": reply, "citations": [], "saved_item_id": None,
            }
        try:
            item = add_item_to_db(cleaned)
        except Exception as exc:
            logger.exception("Assistant failed to save note")
            raise HTTPException(
                status_code=500, detail="I couldn't save that. Please try again."
            ) from exc
        speech = (
            "ठीक है, मैंने इसे सेव कर लिया है।"
            if has_devanagari(transcript)
            else "Okay, I saved that to your inbox."
        )
        return {
            "transcript": transcript,
            "intent": "capture",
            "reply": f"Saved to your inbox: {cleaned}",
            "speech": speech,
            "citations": [],
            "saved_item_id": item["id"],
        }

    if len(cleaned) < 3:
        reply = "Sorry, I didn't catch that."
        return {
            "transcript": transcript, "intent": "clarify", "reply": reply,
            "speech": reply, "citations": [], "saved_item_id": None,
        }
    try:
        answer = build_grounded_answer(cleaned[:500], item_type=None)
    except Exception as exc:
        logger.exception("Assistant failed to answer")
        raise HTTPException(
            status_code=500,
            detail="I couldn't answer that from your inbox. Please try again.",
        ) from exc
    citations = [
        {
            "item_id": c["item_id"],
            "item_type": c["item_type"],
            "title": c["title"],
            "excerpt": (c.get("excerpt") or "")[:200],
        }
        for c in answer.get("citations", [])[:3]
    ]
    return {
        "transcript": transcript,
        "intent": "ask",
        "reply": answer["answer"],
        "speech": to_speech(answer["answer"]),
        "confidence": answer.get("confidence"),
        "citations": citations,
        "saved_item_id": None,
    }


@router.post("/api/assistant/speak")
def assistant_speak(payload: SpeakRequest):
    """Turn reply text into speech (MP3). 501 means: use the browser voice."""
    try:
        audio, content_type = synthesize_speech(payload.text)
    except SpeechError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected speech error")
        raise HTTPException(status_code=502, detail="Voice synthesis failed.") from exc
    return Response(
        content=audio,
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
    )
