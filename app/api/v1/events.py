from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key, verify_track_key
from app.core.config import settings
from app.core.database import get_db
from app.models.entity import Entity
from app.models.event import Event
from app.models.persona import Persona
from app.models.session import Session
from app.schemas.persona import EventCreate, EventResponse

# No router-level auth — each endpoint declares its own so /track can use write_key
router = APIRouter(tags=["events"])


class IdentifyRequest(BaseModel):
    """Merge an anonymous tracked persona into a known distinct_id."""

    anon_id: str = Field(..., max_length=255)
    distinct_id: str = Field(..., max_length=255)
    properties: dict[str, Any] = Field(default_factory=dict)


class IdentifyResponse(BaseModel):
    distinct_id: str
    anon_id: str
    merged: bool
    persona_id: str


def _profile_properties(body: IdentifyRequest) -> dict[str, str]:
    props: dict[str, str] = {}
    for key, value in (body.properties or {}).items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            raw = str(value).strip()
            if raw:
                props[str(key).strip()[:255]] = raw[:2000]
    if body.distinct_id and "email" not in props and "@" in body.distinct_id:
        props["email"] = body.distinct_id
    return props


async def _apply_identify_profile(
    db: AsyncSession, persona: Persona, body: IdentifyRequest
) -> None:
    props = _profile_properties(body)
    if not props:
        return

    display_name = props.get("name") or props.get("full_name")
    if display_name and not persona.name:
        persona.name = display_name[:255]

    existing = {
        row[0]: row[1]
        for row in (
            await db.execute(select(Entity.key, Entity).where(Entity.persona_id == persona.id))
        ).all()
    }
    for key, value in props.items():
        if not key:
            continue
        current = existing.get(key)
        if current:
            current.value = value
        else:
            db.add(Entity(persona_id=persona.id, key=key, value=value))


def _header_scope(request: Request) -> dict:
    def _v(name: str) -> str | None:
        raw = request.headers.get(name)
        if raw is None:
            return None
        trimmed = raw.strip()
        return trimmed or None

    return {
        "organization_id": _v("x-tpt-organization-id"),
        "organization_name": _v("x-tpt-organization-name"),
        "organization_domain": _v("x-tpt-organization-domain"),
        "project_id": _v("x-tpt-project-id"),
        "project_name": _v("x-tpt-project-name"),
    }



def _project_name_score(name: Optional[str]) -> int:
    normalized = (name or "").strip().lower()
    if not normalized:
        return 0
    # Placeholder labels should not override a previously observed real name
    # for the same project_id. This is generic: if a client's real project is
    # actually named "Test", it is still kept unless a better label exists.
    if normalized in {"test", "demo", "default", "unknown", "project"}:
        return 1
    return 10 + min(len(normalized), 80)


async def _best_known_project_name(
    db: AsyncSession, project_id: Optional[str], incoming_name: Optional[str]
) -> Optional[str]:
    if not project_id:
        return incoming_name

    best_name = (incoming_name or "").strip() or None
    best_score = _project_name_score(best_name)

    result = await db.execute(
        select(Event.properties)
        .where(
            Event.properties.is_not(None),
            Event.properties.icontains(f'"project_id": "{project_id}"'),
        )
        .order_by(Event.timestamp.desc())
        .limit(200)
    )
    for raw_props in result.scalars().all():
        try:
            props = json.loads(raw_props) if raw_props else {}
        except json.JSONDecodeError:
            continue
        if str(props.get("project_id") or "").strip() != project_id:
            continue
        candidate = str(props.get("project_name") or props.get("project") or "").strip()
        score = _project_name_score(candidate)
        if candidate and score > best_score:
            best_name = candidate
            best_score = score

    return best_name

async def upload_screenshot(b64: str) -> Optional[str]:
    """Upload a base64-encoded image to Supabase Storage and return the public URL.

    Falls back to storing as a data URL when Supabase Storage is not configured,
    so thumbnails always show in the dashboard regardless of infra setup.
    SHA-256 dedup avoids storing the same image twice in Supabase Storage.
    """
    import logging
    _log = logging.getLogger(__name__)

    if not settings.supabase_url or not settings.supabase_service_key:
        # No Supabase configured — store inline as a data URL so the dashboard
        # can still render the thumbnail via <img src="data:image/jpeg;base64,...">
        _log.debug("upload_screenshot: no Supabase credentials, storing as data URL")
        return f"data:image/jpeg;base64,{b64}"
    try:
        from supabase import create_client  # lazy import
        img_bytes = base64.b64decode(b64)
        file_hash = hashlib.sha256(img_bytes).hexdigest()
        path = f"{file_hash}.jpg"
        client = create_client(settings.supabase_url, settings.supabase_service_key)
        existing = client.storage.from_("screenshots").list("", {"search": path})
        already_exists = any(f.get("name") == path for f in (existing or []))
        if not already_exists:
            client.storage.from_("screenshots").upload(
                path, img_bytes, {"content-type": "image/jpeg", "upsert": "false"}
            )
        public_url = client.storage.from_("screenshots").get_public_url(path)
        _log.info("upload_screenshot: saved %s → %s", path, public_url)
        return public_url
    except Exception as exc:
        _log.error("upload_screenshot failed: %s — falling back to data URL", exc)
        return f"data:image/jpeg;base64,{b64}"


@router.post("/track", response_model=EventResponse, status_code=201)
async def track_event(
    body: EventCreate,
    request: Request,
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

    scope = {k: v for k, v in _header_scope(request).items() if v is not None}
    merged_props = dict(scope)
    if body.properties:
        merged_props.update(body.properties)
    best_project_name = await _best_known_project_name(
        db,
        merged_props.get("project_id"),
        merged_props.get("project_name") or merged_props.get("project"),
    )
    if best_project_name:
        merged_props["project_name"] = best_project_name

    event = Event(
        persona_id=persona.id,
        event_type=body.event_type,
        properties=json.dumps(merged_props) if merged_props else None,
        timestamp=body.timestamp or datetime.now(timezone.utc),
        screenshot_url=screenshot_url,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    return _event_to_response(event)


@router.post("/identify", response_model=IdentifyResponse)
async def identify_persona(
    body: IdentifyRequest,
    _: str = Depends(verify_track_key),
    db: AsyncSession = Depends(get_db),
):
    """Merge an anonymous persona into a known identity.

    Signed-out visitors are tracked under a stable ``anon_*`` id. When a real
    user signs in, move the anonymous persona's events, sessions, and
    non-conflicting entities onto the known ``distinct_id`` so the full
    pre-login journey remains attached to the user.
    """
    anon_id = body.anon_id.strip()
    distinct_id = body.distinct_id.strip()
    if not anon_id or not distinct_id:
        raise HTTPException(status_code=400, detail="anon_id and distinct_id are required")
    identify_properties = {"anon_id": anon_id, "merged": False, **_profile_properties(body)}

    known_result = await db.execute(select(Persona).where(Persona.distinct_id == distinct_id))
    known = known_result.scalar_one_or_none()

    anon_result = await db.execute(select(Persona).where(Persona.distinct_id == anon_id))
    anon = anon_result.scalar_one_or_none()

    # Same id / no anonymous record yet: ensure the known persona exists and log
    # the identify call as a successful no-op.
    if anon_id == distinct_id or not anon:
        if not known:
            known = Persona(distinct_id=distinct_id)
            db.add(known)
            await db.flush()
        await _apply_identify_profile(db, known, body)
        db.add(
            Event(
                persona_id=known.id,
                event_type="identify",
                properties=json.dumps(identify_properties),
                timestamp=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        await db.refresh(known)
        return IdentifyResponse(
            distinct_id=distinct_id,
            anon_id=anon_id,
            merged=False,
            persona_id=known.id,
        )

    # If the known identity doesn't exist, preserve the anonymous persona's row
    # and just rename it. This keeps all foreign keys intact.
    if not known:
        anon.distinct_id = distinct_id
        identify_properties["merged"] = True
        await _apply_identify_profile(db, anon, body)
        db.add(
            Event(
                persona_id=anon.id,
                event_type="identify",
                properties=json.dumps(identify_properties),
                timestamp=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        await db.refresh(anon)
        return IdentifyResponse(
            distinct_id=distinct_id,
            anon_id=anon_id,
            merged=True,
            persona_id=anon.id,
        )

    # Known and anonymous personas both exist. Re-parent anonymous history.
    anon_events = (
        await db.execute(select(Event).where(Event.persona_id == anon.id))
    ).scalars().all()
    for event in anon_events:
        event.persona_id = known.id

    anon_sessions = (
        await db.execute(select(Session).where(Session.persona_id == anon.id))
    ).scalars().all()
    for session in anon_sessions:
        session.persona_id = known.id

    known_entity_keys = {
        row[0]
        for row in (
            await db.execute(select(Entity.key).where(Entity.persona_id == known.id))
        ).all()
    }
    anon_entities = (
        await db.execute(select(Entity).where(Entity.persona_id == anon.id))
    ).scalars().all()
    for entity in anon_entities:
        if entity.key in known_entity_keys:
            await db.delete(entity)
        else:
            entity.persona_id = known.id
            known_entity_keys.add(entity.key)

    identify_properties["merged"] = True
    await _apply_identify_profile(db, known, body)
    db.add(
        Event(
            persona_id=known.id,
            event_type="identify",
            properties=json.dumps(identify_properties),
            timestamp=datetime.now(timezone.utc),
        )
    )
    await db.delete(anon)
    await db.commit()
    await db.refresh(known)

    return IdentifyResponse(
        distinct_id=distinct_id,
        anon_id=anon_id,
        merged=True,
        persona_id=known.id,
    )


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
