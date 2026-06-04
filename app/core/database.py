import ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models import Base

_connect_args: dict = {}
if settings.database_mode == "supabase":
    server_settings: dict = {}
    if settings.db_schema != "public":
        server_settings["search_path"] = settings.db_schema
    if settings.db_ssl:
        # Supabase requires SSL; asyncpg needs an ssl context rather than a string
        _ssl_ctx = ssl.create_default_context()
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = ssl.CERT_NONE
        _connect_args["ssl"] = _ssl_ctx
    if server_settings:
        _connect_args["server_settings"] = server_settings

engine = create_async_engine(
    settings.effective_database_url,
    echo=False,
    connect_args=_connect_args,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Create all tables. Used for SQLite local dev — Supabase uses Alembic migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add columns that may be missing from older schema versions (idempotent)
        for stmt_pg, stmt_sqlite in [
            (
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS screenshot_url TEXT",
                "ALTER TABLE events ADD COLUMN screenshot_url TEXT",
            ),
            (
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS thumbnail_url TEXT",
                "ALTER TABLE sessions ADD COLUMN thumbnail_url TEXT",
            ),
        ]:
            try:
                await conn.exec_driver_sql(stmt_pg)
            except Exception:
                try:
                    await conn.exec_driver_sql(stmt_sqlite)
                except Exception:
                    pass


async def get_db():
    """FastAPI dependency that provides a database session."""
    async with async_session() as session:
        yield session
