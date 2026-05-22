from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# --- Entity schemas ---


class EntitySet(BaseModel):
    """Set one or more key-value entities on a persona."""

    key: str = Field(..., max_length=255, examples=["plan"])
    value: str = Field(..., examples=["enterprise"])


class EntityResponse(BaseModel):
    id: str
    key: str
    value: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Event schemas ---


class EventCreate(BaseModel):
    """Track an event for a persona."""

    event_type: str = Field(..., max_length=255, examples=["page_view"])
    properties: Optional[Dict] = Field(default=None, examples=[{"page": "/pricing"}])
    timestamp: Optional[datetime] = Field(
        default=None, description="ISO 8601 timestamp. Defaults to now if omitted."
    )
    screenshot: Optional[str] = Field(default=None, description="Base64-encoded PNG screenshot")


class EventResponse(BaseModel):
    id: str
    event_type: str
    properties: Optional[Dict] = None
    timestamp: datetime
    screenshot_url: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Persona schemas ---


class PersonaCreate(BaseModel):
    """Create a new persona (tracked identity)."""

    distinct_id: str = Field(
        ..., max_length=255, description="Your unique user identifier (user ID, email, etc.)"
    )
    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    entities: Optional[List[EntitySet]] = Field(
        default=None, description="Initial key-value entities to set on the persona."
    )


class PersonaUpdate(BaseModel):
    """Update a persona's core fields."""

    name: Optional[str] = None
    description: Optional[str] = None


class PersonaResponse(BaseModel):
    id: str
    distinct_id: str
    name: Optional[str]
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    entities: List[EntityResponse] = []

    model_config = {"from_attributes": True}


class PersonaListResponse(BaseModel):
    results: List[PersonaResponse]
    count: int
