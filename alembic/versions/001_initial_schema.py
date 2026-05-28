"""Initial schema: create all TPT tables

Revision ID: 001
Revises:
Create Date: 2026-05-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "personas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("distinct_id", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_anonymous", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_personas_distinct_id", "personas", ["distinct_id"])

    op.create_table(
        "entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "persona_id",
            sa.String(36),
            sa.ForeignKey("personas.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("persona_id", "key", name="uq_persona_entity_key"),
    )
    op.create_index("ix_entities_persona_id", "entities", ["persona_id"])

    op.create_table(
        "events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "persona_id",
            sa.String(36),
            sa.ForeignKey("personas.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.String(255), nullable=False, index=True),
        sa.Column("properties", sa.Text, nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
        sa.Column("screenshot_url", sa.Text, nullable=True),
    )
    op.create_index("ix_events_persona_id", "events", ["persona_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_timestamp", "events", ["timestamp"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "persona_id",
            sa.String(36),
            sa.ForeignKey("personas.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_sessions_persona_id", "sessions", ["persona_id"])

    op.create_table(
        "session_event_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("events_json", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_session_event_batches_session_id", "session_event_batches", ["session_id"])

    op.create_table(
        "cluster_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("algorithm", sa.String(50), nullable=False),
        sa.Column("params", sa.Text, nullable=True),
        sa.Column("num_clusters", sa.Integer, nullable=False),
        sa.Column("num_personas", sa.Integer, nullable=False),
        sa.Column("silhouette_score", sa.String(20), nullable=True),
        sa.Column("calinski_harabasz", sa.String(20), nullable=True),
        sa.Column("davies_bouldin", sa.String(20), nullable=True),
        sa.Column("cluster_summaries", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "cluster_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("cluster_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "persona_id",
            sa.String(36),
            sa.ForeignKey("personas.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("cluster_label", sa.Integer, nullable=False),
        sa.Column("cluster_name", sa.String(255), nullable=True),
    )
    op.create_index("ix_cluster_assignments_run_id", "cluster_assignments", ["run_id"])
    op.create_index("ix_cluster_assignments_persona_id", "cluster_assignments", ["persona_id"])


def downgrade() -> None:
    op.drop_table("cluster_assignments")
    op.drop_table("cluster_runs")
    op.drop_table("session_event_batches")
    op.drop_table("sessions")
    op.drop_table("events")
    op.drop_table("entities")
    op.drop_table("personas")
