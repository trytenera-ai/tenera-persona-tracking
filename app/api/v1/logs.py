from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key
from app.core.database import get_db
from app.models.entity import Entity
from app.models.event import Event
from app.models.persona import Persona

router = APIRouter(prefix="/logs", tags=["logs"])


def _looks_like_email(value: Optional[str]) -> bool:
    if not value:
        return False
    raw = value.strip()
    return "@" in raw and "." in raw.rsplit("@", 1)[-1]


def _is_uuid_like(value: Optional[str]) -> bool:
    if not value:
        return False
    raw = value.strip().lower()
    parts = raw.split("-")
    return (
        len(parts) == 5
        and [len(p) for p in parts] == [8, 4, 4, 4, 12]
        and all(all(c in "0123456789abcdef" for c in p) for p in parts)
    )


def _metadata_email(meta: dict) -> Optional[str]:
    for key in (
        "email",
        "user_email",
        "userEmail",
        "auth_email",
        "primary_email",
        "preferred_email",
    ):
        value = meta.get(key)
        if isinstance(value, str) and _looks_like_email(value):
            return value.strip()
    return None


def _persona_display_id(persona: Optional[object], meta: dict) -> Optional[str]:
    metadata_email = _metadata_email(meta)
    if metadata_email:
        return metadata_email
    if not persona:
        return None
    distinct_id = getattr(persona, "distinct_id", None)
    name = getattr(persona, "name", None)
    email = getattr(persona, "email", None)
    if _looks_like_email(email):
        return email.strip()
    if _looks_like_email(distinct_id):
        return distinct_id
    if _looks_like_email(name):
        return name.strip()
    email_entity_keys = {"email", "user_email", "useremail", "auth_email", "primary_email"}
    for entity in getattr(persona, "entities", None) or []:
        key = (getattr(entity, "key", None) or "").strip().lower()
        value = getattr(entity, "value", None)
        if key in email_entity_keys and _looks_like_email(value):
            return value.strip()
    if _is_uuid_like(distinct_id):
        return "Unknown user"
    return name or distinct_id


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
    hours: Optional[int] = Query(default=24, ge=1, description="Time window in hours; 0 = all time"),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours) if hours else None

    q = select(func.count(Event.id))
    if cutoff is not None:
        q = q.where(Event.timestamp >= cutoff)

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

    label = f"{hours}h" if hours else "all"
    return {
        "total_24h": total,
        "saved_24h": total,
        "failed_24h": 0,
        "success_rate_24h": 1.0 if total > 0 else 0.0,
        "failed_events_24h": 0,
        "label": label,
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
    hours: Optional[int] = Query(default=None, ge=1, description="Time window in hours; omit for all time"),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    # All stored events are saved=True; return empty list when saved=false
    if saved is False:
        return []

    q = (
        select(Event, Persona.id, Persona.distinct_id, Persona.name)
        .outerjoin(Persona, Event.persona_id == Persona.id)
        .order_by(Event.timestamp.desc())
        .limit(limit)
    )

    if hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        q = q.where(Event.timestamp >= cutoff)

    prefix_clause = _exclude_prefixes_clause(exclude_prefixes, exclude_prefixes_logic)
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

    rows = (await db.execute(q)).all()
    persona_ids = [row[1] for row in rows if row[1]]
    email_by_persona_id: dict[str, str] = {}
    if persona_ids:
        email_entity_keys = {
            "email",
            "user_email",
            "useremail",
            "auth_email",
            "primary_email",
        }
        entity_rows = (
            await db.execute(
                select(Entity.persona_id, Entity.value)
                .where(Entity.persona_id.in_(persona_ids))
                .where(func.lower(Entity.key).in_(email_entity_keys))
            )
        ).all()
        for persona_id, value in entity_rows:
            if persona_id not in email_by_persona_id and _looks_like_email(value):
                email_by_persona_id[persona_id] = value.strip()

    import json
    result = []
    for e, persona_id, persona_distinct_id, persona_name in rows:
        try:
            meta = json.loads(e.properties) if e.properties else {}
        except Exception:
            meta = {}
        persona_view = None
        if persona_distinct_id:
            persona_view = type(
                "PersonaDisplay",
                (),
                {
                    "distinct_id": persona_distinct_id,
                    "name": persona_name,
                    "email": email_by_persona_id.get(persona_id),
                },
            )()
        display_id = _persona_display_id(persona_view, meta)
        result.append({
            "id": e.id,
            "action": e.event_type,
            "distinct_id": persona_distinct_id,
            "display_id": display_id,
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
