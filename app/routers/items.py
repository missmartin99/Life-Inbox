"""Inbox items, notes, and file capture routes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

import app.capture.pipeline as pipeline
from app.capture.image_extract import validate_image_upload
from app.capture.pdf_extract import validate_pdf_upload
from app.capture.storage import (
    resolve_storage_path,
    store_audio_bytes,
    store_image_bytes,
    store_pdf_bytes,
    thumbnail_relative_path,
)
from app.capture.url_fetch import validate_public_http_url
from app.capture.voice_extract import validate_audio_upload
from app.database import (
    add_image_item_to_db as _default_add_image_item_to_db,
    add_item_to_db,
    add_note_to_db as _default_add_note_to_db,
    add_pdf_item_to_db,
    add_url_item_to_db,
    add_voice_item_to_db as _default_add_voice_item_to_db,
    delete_item_from_db,
    delete_note_from_db as _default_delete_note_from_db,
    get_item_from_db,
    list_items_from_db,
    list_notes_from_db as _default_list_notes_from_db,
    update_item_extracted_text,
    update_item_in_db,
    update_note_in_db as _default_update_note_in_db,
)
from app.routers._helpers import queue_webhooks, resolve, trigger_auto_analysis
from app.schemas import (
    ExtractedTextUpdate,
    InboxItem,
    InboxItemCreate,
    InboxItemUpdate,
    Note,
    NoteCreate,
    NoteUpdate,
    Priority,
    UrlItemCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["items"])


@router.post("/api/items", response_model=InboxItem)
def create_item(payload: InboxItemCreate, background_tasks: BackgroundTasks):
    try:
        item = add_item_to_db(payload.raw_text)
        trigger_auto_analysis()
        queue_webhooks(background_tasks, "item.created", item)
        return item
    except Exception as exc:
        logger.exception("Failed to save inbox item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save item. Please try again.",
        ) from exc


@router.post("/api/items/url", response_model=InboxItem)
def create_url_item(payload: UrlItemCreate, background_tasks: BackgroundTasks):
    validator = resolve("validate_public_http_url", validate_public_http_url)
    try:
        url = validator(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        item = add_url_item_to_db(url)
    except Exception as exc:
        logger.exception("Failed to save URL item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save URL. Please try again.",
        ) from exc

    processed = pipeline.process_url_item(item["id"])
    trigger_auto_analysis()
    queue_webhooks(background_tasks, "item.created", processed)
    return processed


@router.post("/api/items/pdf", response_model=InboxItem)
async def create_pdf_item(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    data = await file.read()
    try:
        safe_filename = validate_pdf_upload(
            file.filename or "document.pdf",
            file.content_type,
            data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        storage_path = store_pdf_bytes(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to store PDF upload")
        raise HTTPException(
            status_code=500,
            detail="Unable to store PDF. Please try again.",
        ) from exc

    mime_type = (file.content_type or "application/pdf").split(";", 1)[0].strip()
    try:
        item = add_pdf_item_to_db(safe_filename, storage_path, mime_type)
    except Exception as exc:
        logger.exception("Failed to save PDF item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save PDF. Please try again.",
        ) from exc

    processed = pipeline.process_pdf_item(item["id"])
    trigger_auto_analysis()
    queue_webhooks(background_tasks, "item.created", processed)
    return processed


@router.post("/api/items/image", response_model=InboxItem)
async def create_image_item(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    data = await file.read()
    try:
        safe_filename, detected_mime, extension = validate_image_upload(
            file.filename or "image.png",
            file.content_type,
            data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        storage_path = store_image_bytes(data, extension)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to store image upload")
        raise HTTPException(
            status_code=500,
            detail="Unable to store image. Please try again.",
        ) from exc

    mime_type = detected_mime
    if file.content_type:
        declared = file.content_type.split(";", 1)[0].strip().lower()
        if declared in {
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/webp",
            "image/gif",
        }:
            mime_type = declared
    try:
        add_fn = resolve("add_image_item_to_db", _default_add_image_item_to_db)
        item = add_fn(safe_filename, storage_path, mime_type)
    except Exception as exc:
        logger.exception("Failed to save image item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save image. Please try again.",
        ) from exc

    processed = pipeline.process_image_item(item["id"])
    trigger_auto_analysis()
    queue_webhooks(background_tasks, "item.created", processed)
    return processed


@router.post("/api/items/voice", response_model=InboxItem)
async def create_voice_item(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    data = await file.read()
    try:
        safe_filename, detected_mime, extension = validate_audio_upload(
            file.filename or "voice-note.webm",
            file.content_type,
            data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        storage_path = store_audio_bytes(data, extension)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to store voice upload")
        raise HTTPException(
            status_code=500,
            detail="Unable to store voice note. Please try again.",
        ) from exc

    mime_type = detected_mime
    if file.content_type:
        declared = file.content_type.split(";", 1)[0].strip().lower()
        if declared in {
            "audio/webm",
            "audio/wav",
            "audio/x-wav",
            "audio/wave",
            "audio/mpeg",
            "audio/mp3",
            "audio/mp4",
            "audio/m4a",
            "audio/x-m4a",
            "audio/ogg",
            "video/webm",
        }:
            mime_type = declared if declared != "video/webm" else "audio/webm"
    try:
        add_fn = resolve("add_voice_item_to_db", _default_add_voice_item_to_db)
        item = add_fn(safe_filename, storage_path, mime_type)
    except Exception as exc:
        logger.exception("Failed to save voice item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save voice note. Please try again.",
        ) from exc

    processed = pipeline.process_voice_item(item["id"])
    trigger_auto_analysis()
    queue_webhooks(background_tasks, "item.created", processed)
    return processed


@router.get("/api/items/{item_id}/file")
def get_item_source_file(
    item_id: int,
    variant: Optional[str] = Query(None, max_length=20),
):
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    if item["item_type"] not in {"pdf", "image", "voice"} or not item.get(
        "storage_path"
    ):
        raise HTTPException(
            status_code=404,
            detail="No stored file for this item.",
        )
    relative = item["storage_path"]
    media_type = item.get("mime_type") or "application/octet-stream"
    filename = item.get("original_filename") or "download"
    if variant == "thumb" and item["item_type"] == "image":
        relative = thumbnail_relative_path(item["storage_path"])
        media_type = "image/jpeg"
        filename = f"thumb_{Path(filename).stem}.jpg"
    try:
        path = resolve_storage_path(relative)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Stored file not found.") from exc
    if item["item_type"] == "pdf" and not media_type:
        media_type = "application/pdf"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
    )


@router.patch("/api/items/{item_id}/extracted-text", response_model=InboxItem)
def patch_extracted_text(item_id: int, payload: ExtractedTextUpdate):
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    if item["item_type"] not in {"image", "voice", "pdf"}:
        raise HTTPException(
            status_code=400,
            detail="Extracted text can only be edited for image, voice, or PDF items.",
        )
    updated = update_item_extracted_text(item_id, payload.extracted_text)
    if updated is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    trigger_auto_analysis()
    return updated


@router.post("/api/items/{item_id}/retry", response_model=InboxItem)
def retry_item_processing(item_id: int):
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    if item["item_type"] not in {"url", "pdf", "image", "voice"}:
        raise HTTPException(
            status_code=400,
            detail="Only capture items (URL/PDF/image/voice) can be retried here.",
        )
    if item["processing_status"] == "ready":
        return item
    processed = pipeline.process_item(item_id)
    trigger_auto_analysis()
    return processed


@router.get("/api/items", response_model=List[InboxItem])
def get_items(
    search: Optional[str] = Query(None, alias="q", max_length=200),
    item_type: Optional[str] = Query(None, max_length=100),
    category: Optional[str] = Query(None, max_length=100),
    priority: Optional[Priority] = Query(None),
    processing_status: Optional[str] = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query("oldest", pattern="^(oldest|newest)$"),
):
    try:
        return list_items_from_db(
            search=search,
            item_type=item_type,
            category=category,
            priority=priority,
            processing_status=processing_status,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except Exception as exc:
        logger.exception("Failed to load inbox items")
        raise HTTPException(
            status_code=500,
            detail="Unable to load items. Please try again.",
        ) from exc


@router.get("/api/items/{item_id}", response_model=InboxItem)
def read_item(item_id: int):
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    return item


@router.put("/api/items/{item_id}", response_model=InboxItem)
def update_item(item_id: int, payload: InboxItemUpdate):
    try:
        item = update_item_in_db(item_id, payload.raw_text)
    except Exception as exc:
        logger.exception("Failed to update inbox item")
        raise HTTPException(
            status_code=500,
            detail="Unable to update item. Please try again.",
        ) from exc

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    trigger_auto_analysis()
    return item


@router.delete(
    "/api/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_item(item_id: int):
    try:
        deleted = delete_item_from_db(item_id)
    except Exception as exc:
        logger.exception("Failed to delete inbox item")
        raise HTTPException(
            status_code=500,
            detail="Unable to delete item. Please try again.",
        ) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Item not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/notes", response_model=Note, deprecated=True)
def create_note(payload: NoteCreate):
    try:
        add_fn = resolve("add_note_to_db", _default_add_note_to_db)
        note = add_fn(payload.text)
    except Exception as exc:
        logger.exception("Failed to save note")
        raise HTTPException(
            status_code=500,
            detail="Unable to save note. Please try again.",
        ) from exc
    trigger_auto_analysis()
    return note


@router.get("/api/notes", response_model=List[Note], deprecated=True)
def get_notes():
    try:
        list_fn = resolve("list_notes_from_db", _default_list_notes_from_db)
        return list_fn()
    except Exception as exc:
        logger.exception("Failed to load notes")
        raise HTTPException(
            status_code=500,
            detail="Unable to load notes. Please try again.",
        ) from exc


@router.put("/api/notes/{note_id}", response_model=Note, deprecated=True)
def update_note(note_id: int, payload: NoteUpdate):
    try:
        update_fn = resolve("update_note_in_db", _default_update_note_in_db)
        note = update_fn(note_id, payload.text)
    except Exception as exc:
        logger.exception("Failed to update note")
        raise HTTPException(
            status_code=500,
            detail="Unable to update note. Please try again.",
        ) from exc

    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    trigger_auto_analysis()
    return note


@router.delete(
    "/api/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    deprecated=True,
)
def delete_note(note_id: int):
    try:
        del_fn = resolve("delete_note_from_db", _default_delete_note_from_db)
        deleted = del_fn(note_id)
    except Exception as exc:
        logger.exception("Failed to delete note")
        raise HTTPException(
            status_code=500,
            detail="Unable to delete note. Please try again.",
        ) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
