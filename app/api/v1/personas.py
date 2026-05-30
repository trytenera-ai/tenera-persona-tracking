from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key
from app.core.database import get_db
from app.models.entity import Entity
from app.models.event import Event
from app.models.persona import Persona
from app.models.session import Session
from app.schemas.persona import (
    EntityResponse,
    EntitySet,
    PersonaCreate,
    PersonaListResponse,
    PersonaResponse,
    PersonaUpdate,
)

router = APIRouter(prefix="/personas", tags=["personas"], dependencies=[Depends(verify_api_key)])


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


def _session_env_clause(env: str):
    url = Session.url
    staging = or_(
        url.icontains("staging."),
        url.icontains("localhost"),
        url.icontains(":3001"),
    )
    if env == "staging":
        return staging
    return or_(url.is_(None), not_(staging))


def _event_property_clause(key: str, value: str):
    return or_(
        Event.properties.icontains(f'"{key}":"{value}"'),
        Event.properties.icontains(f'"{key}": "{value}"'),
    )


def _persona_scope_exists(
    organization_name: Optional[str],
    project_name: Optional[str],
    project_id: Optional[str] = None,
):
    clauses = [Event.persona_id == Persona.id]
    if organization_name:
        clauses.append(
            or_(
                _event_property_clause("organization_name", organization_name),
                _event_property_clause("org_name", organization_name),
            )
        )
    if project_id:
        clauses.append(_event_property_clause("project_id", project_id))
    elif project_name:
        clauses.append(
            or_(
                _event_property_clause("project_name", project_name),
                _event_property_clause("project", project_name),
            )
        )
    return exists().where(*clauses)


def _persona_env_exists(env: str):
    return or_(
        exists().where(Event.persona_id == Persona.id, _event_env_clause(env)),
        exists().where(Session.persona_id == Persona.id, _session_env_clause(env)),
    )


# --- Persona CRUD ---


@router.post("", response_model=PersonaResponse, status_code=201)
async def create_persona(body: PersonaCreate, db: AsyncSession = Depends(get_db)):
    """Create a new persona with an optional set of initial entities."""
    existing = await db.execute(select(Persona).where(Persona.distinct_id == body.distinct_id))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Persona with distinct_id '{body.distinct_id}' already exists",
        )

    persona = Persona(distinct_id=body.distinct_id, name=body.name, description=body.description)
    db.add(persona)
    await db.flush()  # Ensure persona.id is populated before creating entities

    if body.entities:
        for e in body.entities:
            db.add(Entity(persona_id=persona.id, key=e.key, value=e.value))

    await db.commit()
    await db.refresh(persona)
    return persona


@router.get("", response_model=PersonaListResponse)
async def list_personas(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    search: Optional[str] = Query(
        default=None, description="Search by distinct_id or name"
    ),
    env: Optional[str] = Query(default=None, pattern="^(production|staging)$"),
    hide_anonymous: bool = Query(default=False),
    exclude_prefixes: Optional[str] = Query(
        default=None, description="Comma-separated distinct_id prefixes to hide"
    ),
    exclude_prefixes_logic: str = Query(default="or", pattern="^(or|and)$"),
    organization_name: Optional[str] = Query(default=None),
    project_name: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """List personas with optional search, environment, and anonymous filters."""
    query = select(Persona)
    if search:
        query = query.where(
            Persona.distinct_id.icontains(search) | Persona.name.icontains(search)
        )
    if hide_anonymous:
        query = query.where(not_(_is_anonymous_clause()))
    prefix_clause = _exclude_prefixes_clause(exclude_prefixes, exclude_prefixes_logic)
    if prefix_clause is not None:
        query = query.where(prefix_clause)
    if env:
        query = query.where(_persona_env_exists(env))
    if organization_name or project_name or project_id:
        query = query.where(_persona_scope_exists(organization_name, project_name, project_id))

    query = query.order_by(Persona.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    personas = result.scalars().unique().all()
    return PersonaListResponse(results=personas, count=len(personas))


@router.get("/{persona_id}", response_model=PersonaResponse)
async def get_persona(persona_id: str, db: AsyncSession = Depends(get_db)):
    """Get a persona by ID, including all its entities."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return persona


@router.patch("/{persona_id}", response_model=PersonaResponse)
async def update_persona(
    persona_id: str, body: PersonaUpdate, db: AsyncSession = Depends(get_db)
):
    """Update a persona's name or description."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    if body.name is not None:
        persona.name = body.name
    if body.description is not None:
        persona.description = body.description

    await db.commit()
    await db.refresh(persona)
    return persona


@router.delete("/{persona_id}", status_code=204)
async def delete_persona(persona_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a persona and all its entities and events."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    await db.delete(persona)
    await db.commit()


# --- Entity CRUD ---


@router.post("/{persona_id}/entities", response_model=List[EntityResponse])
async def set_entities(
    persona_id: str, body: List[EntitySet], db: AsyncSession = Depends(get_db)
):
    """Set key-value entities on a persona. Existing keys are overwritten."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    results = []
    for item in body:
        # Upsert: find existing or create new
        existing = await db.execute(
            select(Entity).where(Entity.persona_id == persona_id, Entity.key == item.key)
        )
        entity = existing.scalar_one_or_none()
        if entity:
            entity.value = item.value
        else:
            entity = Entity(persona_id=persona_id, key=item.key, value=item.value)
            db.add(entity)
        results.append(entity)

    await db.commit()
    for entity in results:
        await db.refresh(entity)
    return results


@router.get("/{persona_id}/entities", response_model=List[EntityResponse])
async def get_entities(persona_id: str, db: AsyncSession = Depends(get_db)):
    """Get all entities for a persona."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    result = await db.execute(
        select(Entity).where(Entity.persona_id == persona_id).order_by(Entity.key)
    )
    return result.scalars().all()


@router.delete("/{persona_id}/entities/{key}", status_code=204)
async def delete_entity(persona_id: str, key: str, db: AsyncSession = Depends(get_db)):
    """Remove a specific entity from a persona."""
    result = await db.execute(
        select(Entity).where(Entity.persona_id == persona_id, Entity.key == key)
    )
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{key}' not found")

    await db.delete(entity)
    await db.commit()
