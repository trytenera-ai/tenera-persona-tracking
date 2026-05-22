from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings

api_key_header = APIKeyHeader(name="X-API-Key")


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """Full API key — grants access to all endpoints including reads."""
    if api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key


async def verify_track_key(api_key: str = Security(api_key_header)) -> str:
    """Write-only key for the /track endpoint — accepts api_key OR write_key.

    write_key is safe to embed in browser JS since it only allows event ingestion.
    """
    if api_key == settings.api_key:
        return api_key
    if settings.write_key and api_key == settings.write_key:
        return api_key
    raise HTTPException(status_code=401, detail="Invalid API key")
