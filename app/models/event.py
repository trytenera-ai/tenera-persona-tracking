from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, generate_uuid

if TYPE_CHECKING:
    from app.models.persona import Persona


class Event(Base):
    """An event represents a tracked action or state change for a persona.

    Events form the timeline of a persona's activity. Each event has a type
    (e.g. "page_view", "purchase", "plan_upgrade") and optional properties
    stored as a JSON string for flexibility.

    Inspired by PostHog's event model but scoped to persona-level tracking.
    """

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    persona_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    properties: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    screenshot_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    persona: Mapped["Persona"] = relationship("Persona", back_populates="events")
