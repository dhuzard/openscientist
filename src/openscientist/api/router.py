"""
Main API router for OpenScientist REST API.

Combines all API endpoints and adds middleware for rate limiting,
CORS, and error handling.
"""

import logging

from fastapi import APIRouter, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from .endpoints import dvc_router, jobs_router, keys_router, shares_router, skills_router

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
api_router = APIRouter(prefix="/api/v1")

api_router.include_router(jobs_router)
api_router.include_router(keys_router)
api_router.include_router(shares_router)
api_router.include_router(skills_router)
api_router.include_router(dvc_router)


@api_router.get("/health", tags=["Health"])
@limiter.limit("10/minute")
async def health_check(request: Request) -> dict[str, str]:
    _ = request
    return {"status": "ok", "version": "v1", "api": "openscientist"}
