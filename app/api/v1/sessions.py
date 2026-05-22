import json
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key, verify_track_key
from app.core.database import get_db
from app.models.persona import Persona
from app.models.session import Session, SessionEventBatch

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    distinct_id: str
    url: str | None = None


@router.post("", status_code=201)
async def create_session(
    body: SessionCreate,
    _: str = Depends(verify_track_key),
    db: AsyncSession = Depends(get_db),
):
    """Create a new rrweb recording session for a persona."""
    result = await db.execute(select(Persona).where(Persona.distinct_id == body.distinct_id))
    persona = result.scalar_one_or_none()
    if not persona:
        persona = Persona(distinct_id=body.distinct_id)
        db.add(persona)
        await db.flush()

    session = Session(persona_id=persona.id, url=body.url)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {"id": session.id, "started_at": session.created_at.isoformat()}


@router.post("/{session_id}/events", status_code=204)
async def append_session_events(
    session_id: str,
    events: List[Any],
    _: str = Depends(verify_track_key),
    db: AsyncSession = Depends(get_db),
):
    """Append a batch of rrweb events to a session."""
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not events:
        return

    batch = SessionEventBatch(session_id=session_id, events_json=json.dumps(events))
    db.add(batch)
    await db.commit()


@router.get("/{session_id}/events")
async def get_session_events(
    session_id: str,
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Return all rrweb events for a session, sorted by timestamp for replay."""
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = await db.execute(
        select(SessionEventBatch)
        .where(SessionEventBatch.session_id == session_id)
        .order_by(SessionEventBatch.created_at)
    )
    batches = result.scalars().all()

    all_events: List[Any] = []
    for batch in batches:
        try:
            all_events.extend(json.loads(batch.events_json))
        except Exception:
            pass

    all_events.sort(key=lambda e: e.get("timestamp", 0) if isinstance(e, dict) else 0)
    return {"events": all_events}
