from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import init_db

_APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (SQLite and Railway Postgres without external migration runner)
    if settings.database_mode == "sqlite" or not settings.db_ssl:
        await init_db()

    # Start the clustering scheduler
    from app.clustering.scheduler import start_scheduler

    start_scheduler()

    yield


app = FastAPI(
    title="Tenera Persona Tracking",
    description=(
        "Open-source persona tracking and cohort analytics engine. "
        "Track user personas, attach arbitrary entities, and build event timelines. "
        "Designed to integrate seamlessly with Tenera."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# Mount static files and templates for minimal UI
app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))


# --- Minimal UI routes ---


from fastapi import Request


@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"api_key": settings.api_key})


@app.get("/test")
async def test_page(request: Request):
    return templates.TemplateResponse(request, "test-integration.html")


@app.get("/health")
async def health():
    return {"status": "ok"}
