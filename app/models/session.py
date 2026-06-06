from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from app.models.persona import Persona


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    persona_id: Mapped[str] = mapped_column(String(36), ForeignKey("personas.id"), nullable=False, index=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    persona: Mapped["Persona"] = relationship("Persona", back_populates="sessions")
    batches: Mapped[List["SessionEventBatch"]] = relationship(
        "SessionEventBatch", back_populates="session", cascade="all, delete-orphan"
    )


class SessionEventBatch(Base, TimestampMixin):
    """A batch of rrweb events flushed from the client every ~5 seconds."""

    __tablename__ = "session_event_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False, index=True)
    events_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array of rrweb events

    session: Mapped["Session"] = relationship("Session", back_populates="batches")
