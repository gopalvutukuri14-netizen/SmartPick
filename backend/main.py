import os
from typing import Optional
import logging
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.orchestrator import run_pipeline
from backend.auth import auth_router, get_current_user_optional
from backend.database.models import User
from backend.rate_limit import (
    enforce_rate_limit,
    get_client_ip,
    recommend_limit_per_min,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger(__name__)

_DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def parse_cors_origins(raw_origins: Optional[str] = None) -> list[str]:
    """
    Parse and normalize CORS_ORIGINS from a comma-separated environment string.
    Rejects wildcard '*' when credentials are enabled.
    """
    raw = os.getenv("CORS_ORIGINS", "") if raw_origins is None else raw_origins
    if not raw or not raw.strip():
        return list(_DEFAULT_CORS_ORIGINS)

    origins: list[str] = []
    for item in raw.split(","):
        normalized = item.strip().rstrip("/")
        if not normalized:
            continue
        if normalized == "*":
            raise ValueError(
                "Wildcard '*' is not permitted in CORS_ORIGINS when allow_credentials=True."
            )
        if not (normalized.startswith("http://") or normalized.startswith("https://")):
            raise ValueError(
                f"Invalid CORS origin '{normalized}'. Origins must start with http:// or https://."
            )
        if normalized not in origins:
            origins.append(normalized)

    return origins or list(_DEFAULT_CORS_ORIGINS)


app = FastAPI(
    title="SmartPick",
    description="AI-powered retail product recommendation system with explainable AI and authentication",
    version="1.1.0",
)

# Allow configured frontend origins to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Include Authentication Router
app.include_router(auth_router)


# ?? Request / response models ???????????????????????????????????

class QueryRequest(BaseModel):
    query: str


class RecommendRequest(BaseModel):
    """Request body for the full recommendation pipeline."""
    query: str = Field(description="Natural-language shopping query")
    user_id: str = Field(
        default="anonymous",
        description="User profile ID for personalization (e.g. 'camera_focused', 'budget_conscious')",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of products to retrieve and rank",
    )
    previous_products: list[dict] = Field(
        default_factory=list,
        description="Products from the last recommendation, for follow-up/comparison context",
    )
    category: Optional[str] = Field(
        default=None,
        description="Explicit category context selected in frontend",
    )


# ?? Endpoints ????????????????????????????????????????????????????

@app.get("/")
def root():
    return {"message": "SmartPick backend is running"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/understand")
def understand_query(request: QueryRequest):
    """Parse a user query into structured fields (debug endpoint)."""
    # Initialize the agent lazily inside the request so that a missing
    # GEMINI_API_KEY causes a 500 response rather than a startup crash.
    from backend.Agents.query_understanding import QueryUnderstandingAgent
    try:
        agent = QueryUnderstandingAgent()
        result = agent.understand(request.query)
        return result.model_dump()
    except Exception as exc:
        logger.error("understand_query failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Query understanding failed. Please check the API key configuration.",
        )


@app.post("/recommend")
def recommend(
    request: RecommendRequest,
    http_request: Request,
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Full recommendation pipeline.

    Runs every agent in the correct order via the LangGraph
    orchestrator and returns the final structured response.
    If authenticated via JWT, the verified user ID takes precedence over client body.
    """
    # Derive identity securely from authenticated token if logged in
    effective_user_id = current_user.id if current_user else request.user_id

    rate_identifier = (
        f"user:{current_user.id}"
        if current_user
        else f"ip:{get_client_ip(http_request)}"
    )
    enforce_rate_limit(
        scope="recommend",
        identifier=rate_identifier,
        limit=recommend_limit_per_min(),
        window_seconds=60,
    )

    import time
    t0 = time.perf_counter()

    try:
        kwargs = {
            "raw_query": request.query,
            "user_id": effective_user_id,
            "top_k": request.top_k,
            "previous_products": request.previous_products or None,
        }
        if request.category is not None:
            kwargs["category"] = request.category

        result = run_pipeline(**kwargs)
        api_dur_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "endpoint=/recommend status=200 duration_ms=%.1f user=%s category=%s",
            api_dur_ms,
            "auth" if current_user else "anon",
            request.category,
        )
        return result.model_dump()
    except Exception as exc:
        api_dur_ms = (time.perf_counter() - t0) * 1000
        logger.error("recommend endpoint error: %s (duration_ms=%.1f)", exc, api_dur_ms, exc_info=True)
        # Sanitize: do not expose internal exception details to the client.
        raise HTTPException(
            status_code=500,
            detail=(
                "The recommendation pipeline encountered an error. "
                "This may be a temporary issue with the AI service."
            ),
        )
