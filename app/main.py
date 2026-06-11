from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
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
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
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


@app.options("/{path:path}", include_in_schema=False)
async def options_any(path: str):
    # Keep probes/preflight from surfacing as noisy 405s in browser devtools.
    return Response(status_code=204)


@app.head("/{path:path}", include_in_schema=False)
async def head_any(path: str):
    # FastAPI/Starlette can still emit 405 for HEAD on some routes behind proxies.
    return Response(status_code=200)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Without this GET route the catch-all HEAD/OPTIONS handlers make Starlette
    # return 405 (path matches a route, wrong method) instead of 404.
    return Response(status_code=204)


ACCESS_COOKIE = "tpt_access"


def _has_valid_access_code(value: Optional[str]) -> bool:
    expected = (settings.tpt_access_code or "").strip().lower()
    if not expected:
        return True
    return (value or "").strip().lower() == expected


def _access_challenge() -> HTMLResponse:
    return HTMLResponse(
        """
        <!doctype html>
        <html>
        <head>
          <title>Tenera Persona Tracking</title>
          <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
          <style>
            body { margin: 0; min-height: 100vh; display: grid; place-items: center;
                   font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                   background: #f6f1e8; color: #241f1a; }
            main { width: min(92vw, 380px); padding: 28px; border: 1px solid #d8cdbd;
                   border-radius: 18px; background: #fffaf1;
                   box-shadow: 0 16px 40px rgba(36,31,26,.08); }
            h1 { margin: 0 0 8px; font-size: 22px; }
            p { margin: 0 0 20px; color: #6c6258; font-size: 14px; }
            input, button { width: 100%; box-sizing: border-box;
                            border-radius: 10px; font-size: 15px; }
            input { padding: 12px 14px; border: 1px solid #cfc3b1; background: #fff; }
            button { margin-top: 12px; padding: 12px 14px; border: 0;
                     background: #241f1a; color: #fff; font-weight: 650; cursor: pointer; }
          </style>
        </head>
        <body>
          <main>
            <h1>Tenera Persona Tracking</h1>
            <p>Enter the access code to continue.</p>
            <form method=\"get\">
              <input name=\"code\" type=\"password\" autocomplete=\"current-password\" autofocus>
              <button type=\"submit\">Continue</button>
            </form>
          </main>
        </body>
        </html>
        """,
        status_code=401,
    )


def _require_dashboard_access(request: Request) -> Optional[Response]:
    if not settings.tpt_access_code:
        return None
    if request.cookies.get(ACCESS_COOKIE) == "1":
        return None

    url_code = request.query_params.get("code")
    if _has_valid_access_code(url_code):
        remaining_params = [(k, v) for k, v in request.query_params.multi_items() if k != "code"]
        clean_url = request.url.path
        if remaining_params:
            clean_url += "?" + urlencode(remaining_params)
        response = RedirectResponse(clean_url, status_code=303)
        response.set_cookie(ACCESS_COOKIE, "1", max_age=604800, httponly=True, samesite="lax")
        return response

    return _access_challenge()


@app.get("/tpt.js", include_in_schema=False)
async def serve_tpt_snippet():
    return FileResponse(
        _APP_DIR / "static" / "tpt.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=3600"},
    )


@app.get("/docs", include_in_schema=False)
async def docs(request: Request):
    if response := _require_dashboard_access(request):
        return response
    return get_swagger_ui_html(
        openapi_url="/openapi.json", title="Tenera Persona Tracking - Swagger UI"
    )


@app.get("/redoc", include_in_schema=False)
async def redoc(request: Request):
    if response := _require_dashboard_access(request):
        return response
    return get_redoc_html(openapi_url="/openapi.json", title="Tenera Persona Tracking - ReDoc")


@app.get("/openapi.json", include_in_schema=False)
async def openapi(request: Request):
    if response := _require_dashboard_access(request):
        return response
    return app.openapi()


@app.get("/")
async def dashboard(request: Request):
    if response := _require_dashboard_access(request):
        return response
    return templates.TemplateResponse(
        request, "dashboard.html", {"api_key": settings.api_key, "write_key": settings.write_key}
    )


@app.get("/test")
async def test_page(request: Request):
    if response := _require_dashboard_access(request):
        return response
    return templates.TemplateResponse(request, "test-integration.html")


@app.get("/replay/{session_id}")
async def replay_session(request: Request, session_id: str):
    if response := _require_dashboard_access(request):
        return response
    return templates.TemplateResponse(
        request, "replay.html", {"session_id": session_id, "api_key": settings.api_key}
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
