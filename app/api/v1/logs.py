from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import verify_api_key
from app.core.database import get_db
from app.models.event import Event
from app.models.persona import Persona

router = APIRouter(prefix="/logs", tags=["logs"])


def _is_anonymous_clause():
    return or_(
        Persona.distinct_id.startswith("anon_"),
        Persona.distinct_id.startswith("anonymous"),
        func.coalesce(Persona.name, "") == "anonymous",
    )


def _exclude_prefixes_clause(exclude_prefixes: Optional[str], logic: str = "or"):
    if not exclude_prefixes:
        return None
    prefixes = [p.strip().lower() for p in exclude_prefixes.split(",") if p.strip()]
    if not prefixes:
        return None
    clauses = [Persona.distinct_id.ilike(f"{prefix}%") for prefix in prefixes]
    if logic == "and":
        return not_(and_(*clauses))
    return not_(or_(*clauses))


def _event_env_clause(env: str):
    props = Event.properties
    staging = or_(
        props.icontains('"env":"staging"'),
        props.icontains('"env": "staging"'),
        props.icontains("staging."),
        props.icontains("localhost"),
        props.icontains(":3001"),
    )
    if env == "staging":
        return staging
    return or_(props.is_(None), not_(staging))


def _event_property_clause(key: str, value: str):
    return or_(
        Event.properties.icontains(f'"{key}":"{value}"'),
        Event.properties.icontains(f'"{key}": "{value}"'),
    )


def _event_org_clause(org_name: str):
    return or_(
        _event_property_clause("organization_name", org_name),
        _event_property_clause("org_name", org_name),
    )


def _event_project_clause(project_name: str):
    return or_(
        _event_property_clause("project_name", project_name),
        _event_property_clause("project", project_name),
    )


def _event_project_id_clause(project_id: str):
    return _event_property_clause("project_id", project_id)


@router.get("/stats")
async def get_stats(
    distinct_id: Optional[str] = Query(None),
    env: Optional[str] = Query(default=None, pattern="^(production|staging)$"),
    hide_anonymous: bool = Query(default=False),
    exclude_prefixes: Optional[str] = Query(
        default=None, description="Comma-separated distinct_id prefixes to hide"
    ),
    exclude_prefixes_logic: str = Query(default="or", pattern="^(or|and)$"),
    organization_name: Optional[str] = Query(default=None),
    project_name: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    q = (
        select(func.count(Event.id))
        .where(Event.timestamp >= cutoff)
    )
    prefix_clause = _exclude_prefixes_clause(exclude_prefixes, exclude_prefixes_logic)
    if distinct_id or hide_anonymous or prefix_clause is not None:
        q = q.join(Persona)
    if distinct_id:
        q = q.where(Persona.distinct_id == distinct_id)
    if hide_anonymous:
        q = q.where(not_(_is_anonymous_clause()))
    if prefix_clause is not None:
        q = q.where(prefix_clause)
    if env:
        q = q.where(_event_env_clause(env))
    if organization_name:
        q = q.where(_event_org_clause(organization_name))
    if project_id:
        q = q.where(_event_project_id_clause(project_id))
    elif project_name:
        q = q.where(_event_project_clause(project_name))

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
    env: Optional[str] = Query(default=None, pattern="^(production|staging)$"),
    hide_anonymous: bool = Query(default=False),
    exclude_prefixes: Optional[str] = Query(
        default=None, description="Comma-separated distinct_id prefixes to hide"
    ),
    exclude_prefixes_logic: str = Query(default="or", pattern="^(or|and)$"),
    organization_name: Optional[str] = Query(default=None),
    project_name: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
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
    prefix_clause = _exclude_prefixes_clause(exclude_prefixes, exclude_prefixes_logic)
    if distinct_id or hide_anonymous or prefix_clause is not None:
        q = q.join(Persona)
    if distinct_id:
        q = q.where(Persona.distinct_id == distinct_id)
    if hide_anonymous:
        q = q.where(not_(_is_anonymous_clause()))
    if prefix_clause is not None:
        q = q.where(prefix_clause)
    if env:
        q = q.where(_event_env_clause(env))
    if organization_name:
        q = q.where(_event_org_clause(organization_name))
    if project_id:
        q = q.where(_event_project_id_clause(project_id))
    elif project_name:
        q = q.where(_event_project_clause(project_name))

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
            "created_at": e.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
            "saved": True,
            "error": None,
            "metadata": meta,
            "screenshot_url": e.screenshot_url,
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
