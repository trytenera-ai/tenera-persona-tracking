import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key, verify_track_key
from app.core.database import get_db
from app.models.persona import Persona
from app.models.session import Session, SessionEventBatch

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _is_anonymous_clause():
    return or_(
        Persona.distinct_id.startswith("anon_"),
        Persona.distinct_id.startswith("anonymous"),
        func.coalesce(Persona.name, "") == "anonymous",
    )


def _exclude_prefixes_clause(exclude_prefixes: Optional[str]):
    if not exclude_prefixes:
        return None
    prefixes = [p.strip().lower() for p in exclude_prefixes.split(",") if p.strip()]
    if not prefixes:
        return None
    return not_(
        or_(*[Persona.distinct_id.ilike(f"{prefix}%") for prefix in prefixes])
    )


def _session_env_clause(env: str):
    staging = or_(
        Session.url.icontains("staging."),
        Session.url.icontains("localhost"),
        Session.url.icontains(":3001"),
    )
    if env == "staging":
        return staging
    return or_(Session.url.is_(None), not_(staging))


@router.get("")
async def list_sessions(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    env: Optional[str] = Query(default=None, pattern="^(production|staging)$"),
    hide_anonymous: bool = Query(default=False),
    exclude_prefixes: Optional[str] = Query(
        default=None, description="Comma-separated distinct_id prefixes to hide"
    ),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """List sessions and return a total count for dashboard summary cards."""
    base = select(Session).join(Persona)
    count_q = select(func.count(Session.id)).join(Persona)

    filters = []
    if hide_anonymous:
        filters.append(not_(_is_anonymous_clause()))
    prefix_clause = _exclude_prefixes_clause(exclude_prefixes)
    if prefix_clause is not None:
        filters.append(prefix_clause)
    if env:
        filters.append(_session_env_clause(env))

    if filters:
        base = base.where(*filters)
        count_q = count_q.where(*filters)

    total = (await db.execute(count_q)).scalar_one()
    result = await db.execute(
        base.order_by(Session.created_at.desc()).offset(offset).limit(limit)
    )
    sessions = result.scalars().all()
    return {
        "results": [
            {
                "id": s.id,
                "persona_id": s.persona_id,
                "url": s.url,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in sessions
        ],
        "count": total,
    }


class SessionCreate(BaseModel):
    distinct_id: str
    url: Optional[str] = None


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
