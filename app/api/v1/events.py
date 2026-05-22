import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key, verify_track_key
from app.core.config import settings
from app.core.database import get_db
from app.models.event import Event
from app.models.persona import Persona
from app.schemas.persona import EventCreate, EventResponse

# No router-level auth — each endpoint declares its own so /track can use write_key
router = APIRouter(tags=["events"])


async def upload_screenshot(b64: str) -> Optional[str]:
    """Upload a base64-encoded PNG to Supabase Storage and return the public URL."""
    import logging
    _log = logging.getLogger(__name__)

    if not settings.supabase_url or not settings.supabase_service_key:
        _log.warning("upload_screenshot: SUPABASE_URL or SUPABASE_SERVICE_KEY not set — skipping")
        return None
    try:
        from supabase import create_client  # lazy import
        img_bytes = base64.b64decode(b64)
        file_hash = hashlib.sha256(img_bytes).hexdigest()
        path = f"{file_hash}.png"
        client = create_client(settings.supabase_url, settings.supabase_service_key)
        existing = client.storage.from_("screenshots").list("", {"search": path})
        already_exists = any(f.get("name") == path for f in (existing or []))
        if not already_exists:
            client.storage.from_("screenshots").upload(
                path, img_bytes, {"content-type": "image/png", "upsert": "false"}
            )
        public_url = client.storage.from_("screenshots").get_public_url(path)
        _log.info("upload_screenshot: saved %s → %s", path, public_url)
        return public_url
    except Exception as exc:
        _log.error("upload_screenshot failed: %s", exc)
        return None


@router.post("/track", response_model=EventResponse, status_code=201)
async def track_event(
    body: EventCreate,
    distinct_id: str = Query(..., description="The persona's distinct_id to track against"),
    _: str = Depends(verify_track_key),
    db: AsyncSession = Depends(get_db),
):
    """Track an event for a persona by distinct_id.

    This is the primary ingestion endpoint — similar to PostHog's /capture.
    If the persona doesn't exist yet, it will be created automatically.
    """
    screenshot_url: Optional[str] = None
    if body.screenshot:
        screenshot_url = await upload_screenshot(body.screenshot)

    # Find or create persona by distinct_id
    result = await db.execute(select(Persona).where(Persona.distinct_id == distinct_id))
    persona = result.scalar_one_or_none()
    if not persona:
        persona = Persona(distinct_id=distinct_id)
        db.add(persona)
        await db.flush()

    event = Event(
        persona_id=persona.id,
        event_type=body.event_type,
        properties=json.dumps(body.properties) if body.properties else None,
        timestamp=body.timestamp or datetime.now(timezone.utc),
        screenshot_url=screenshot_url,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    return _event_to_response(event)


@router.get("/personas/{persona_id}/events", response_model=List[EventResponse])
async def get_persona_events(
    persona_id: str,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    event_type: Optional[str] = Query(default=None, description="Filter by event type"),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get the event timeline for a persona."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    query = select(Event).where(Event.persona_id == persona_id)
    if event_type:
        query = query.where(Event.event_type == event_type)
    query = query.order_by(Event.timestamp.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    return [_event_to_response(e) for e in result.scalars().all()]


def _event_to_response(event: Event) -> EventResponse:
    """Convert an Event model to an EventResponse, parsing the JSON properties."""
    props = None
    if event.properties:
        try:
            props = json.loads(event.properties)
        except json.JSONDecodeError:
            props = {"_raw": event.properties}

    return EventResponse(
        id=event.id,
        event_type=event.event_type,
        properties=props,
        timestamp=event.timestamp,
        screenshot_url=event.screenshot_url,
    )
