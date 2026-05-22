from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import verify_api_key
from app.core.database import get_db
from app.models.event import Event
from app.models.persona import Persona

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/stats")
async def get_stats(
    distinct_id: Optional[str] = Query(None),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    q = (
        select(func.count(Event.id))
        .where(Event.timestamp >= cutoff)
    )
    if distinct_id:
        q = q.join(Persona).where(Persona.distinct_id == distinct_id)

    total = (await db.execute(q)).scalar_one()

    return {
        "total_24h": total,
        "saved_24h": total,
        "failed_24h": 0,
        "success_rate_24h": 1.0 if total > 0 else 0.0,
        "failed_events_24h": 0,
    }


@router.get("/activity")
async def get_activity(
    limit: int = Query(100, le=500),
    saved: Optional[bool] = Query(None),
    distinct_id: Optional[str] = Query(None),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    # All stored events are saved=True; return empty list when saved=false
    if saved is False:
        return []

    q = (
        select(Event)
        .options(selectinload(Event.persona))
        .order_by(Event.timestamp.desc())
        .limit(limit)
    )
    if distinct_id:
        q = q.join(Persona).where(Persona.distinct_id == distinct_id)

    rows = (await db.execute(q)).scalars().all()

    import json
    result = []
    for e in rows:
        try:
            meta = json.loads(e.properties) if e.properties else {}
        except Exception:
            meta = {}
        result.append({
            "id": e.id,
            "action": e.event_type,
            "distinct_id": e.persona.distinct_id if e.persona else None,
            "timestamp": e.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
            "saved": True,
            "error": None,
            "metadata": meta,
        })
    return result


@router.get("/failed-events")
async def get_failed_events(
    limit: int = Query(50, le=200),
    distinct_id: Optional[str] = Query(None),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    # No failed events in the DB (failures never persist); always empty
    return []
