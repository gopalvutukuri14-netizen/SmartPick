"""
Regression tests for critical & high-priority deployment readiness fixes:
1. JWT secret fail-fast validation
2. Environment-based CORS origin parsing and preflight enforcement
3. In-process API rate limiting (429 + Retry-After + bounded memory + untrusted proxy header safety)
4. Generalized brand matching (capitalization, multiple preferred, excluded, non-Samsung)
5. Gemini failure handling (non-retryable auth errors, network retries, conservative QU fallback)
6. LangGraph state cleanliness (no Future in PipelineState)
7. Bounded LRU product image cache
"""

from unittest.mock import MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from backend.main import app, parse_cors_origins
from backend.auth.security import validate_jwt_secret
from backend.rate_limit import InMemoryRateLimiter, rate_limiter
from backend.Agents.gemini_retry import call_with_retry
from backend.Agents.query_understanding.agent import QueryUnderstandingAgent
from backend.Agents.query_understanding.schema import Intent, RetailCategory, BrandPreferences
from backend.Agents.retrieval.agent import RetrievalAgent
from backend.Agents.query_understanding.schema import QueryUnderstanding
from backend.Agents.ranking.schema import RankingResult, RankedProduct, ScoreBreakdown
from backend.orchestrator.state import PipelineState
from backend.orchestrator.nodes import ranking_node
from backend.pipeline import product_images


@pytest.fixture(autouse=True)
def reset_limiter():
    rate_limiter.reset()
    yield
    rate_limiter.reset()


# ===================================================================
# 1. JWT SECRET VALIDATION
# ===================================================================

def test_jwt_secret_missing_fails_fast_outside_testing():
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY is not configured"):
        validate_jwt_secret("", allow_test_fallback=False)


def test_jwt_secret_placeholder_fails_fast_outside_testing():
    with pytest.raises(RuntimeError, match="insecure placeholder"):
        validate_jwt_secret("smartpick-insecure-dev-secret-key-change-in-prod-32bytes", allow_test_fallback=False)


def test_jwt_secret_short_fails_fast():
    with pytest.raises(RuntimeError, match="too short"):
        validate_jwt_secret("short-secret-123", allow_test_fallback=False)


def test_jwt_secret_valid_accepted():
    valid = "a" * 32
    assert validate_jwt_secret(valid, allow_test_fallback=False) == valid


# ===================================================================
# 2. ENVIRONMENT-BASED CORS
# ===================================================================

def test_parse_cors_origins_normalizes_and_deduplicates():
    origins = parse_cors_origins(" https://smartpick.vercel.app/ , http://localhost:5173 , https://smartpick.vercel.app ")
    assert origins == ["https://smartpick.vercel.app", "http://localhost:5173"]


def test_parse_cors_origins_rejects_wildcard():
    with pytest.raises(ValueError, match="Wildcard"):
        parse_cors_origins("*")


def test_parse_cors_origins_rejects_invalid_scheme():
    with pytest.raises(ValueError, match="Invalid CORS origin"):
        parse_cors_origins("ftp://example.com")


def test_cors_disallowed_origin_rejected_by_middleware():
    client = TestClient(app)
    res = client.options(
        "/recommend",
        headers={
            "Origin": "https://evil-attacker.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert res.headers.get("access-control-allow-origin") is None


# ===================================================================
# 3. API RATE LIMITING
# ===================================================================

def test_rate_limiter_sliding_window_and_bounded_keys():
    limiter = InMemoryRateLimiter(max_keys=100)
    for _ in range(3):
        allowed, retry = limiter.check_and_record("k1", limit=3, window_seconds=60, now=100.0)
        assert allowed is True
        assert retry == 0

    allowed, retry = limiter.check_and_record("k1", limit=3, window_seconds=60, now=101.0)
    assert allowed is False
    assert retry >= 1

    # Window expires
    allowed_later, _ = limiter.check_and_record("k1", limit=3, window_seconds=60, now=161.0)
    assert allowed_later is True

    # Bounded eviction
    for i in range(150):
        limiter.check_and_record(f"evict:{i}", limit=5, window_seconds=60, now=200.0)
    assert len(limiter._buckets) <= 100


def test_recommend_rate_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_RECOMMEND_PER_MIN", "2")
    client = TestClient(app)

    with patch("backend.main.run_pipeline") as mock_pipe:
        from backend.Agents.response.schema import ResponseResult
        mock_pipe.return_value = ResponseResult(summary="ok", recommendations=[], follow_up_suggestions=[])

        assert client.post("/recommend", json={"query": "hello"}).status_code == 200
        assert client.post("/recommend", json={"query": "hello"}).status_code == 200

        # Spoofed X-Forwarded-For must NOT bypass IP rate limit
        res = client.post(
            "/recommend",
            json={"query": "hello"},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert res.status_code == 429
        assert "Retry-After" in res.headers
        assert "too many requests" in res.json()["detail"].lower()


def test_auth_login_rate_limit_returns_429(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AUTH_ACCOUNT_PER_MIN", "2")
    from backend.database.models import Base
    from backend.database.session import get_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)
        payload = {"username_or_email": "brute@example.com", "password": "WrongPassword1"}
        assert client.post("/auth/login", json=payload).status_code == 401
        assert client.post("/auth/login", json=payload).status_code == 401
        res = client.post("/auth/login", json=payload)
        assert res.status_code == 429
        assert "Retry-After" in res.headers
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


# ===================================================================
# 4. GENERALIZED BRAND MATCHING
# ===================================================================

def test_generalized_brand_matching_capitalization_and_multiple_brands():
    agent = object.__new__(RetrievalAgent)
    qu = QueryUnderstanding(
        raw_query="Sony or Apple headphones",
        intent=Intent.PRODUCT_RECOMMENDATION,
        category=RetailCategory.ELECTRONICS,
        product_type="headphones",
        brand=BrandPreferences(preferred=["sony", "t-fal"], excluded=["apple"]),
    )
    where = agent._build_where_filter(qu)
    and_clauses = where["$and"]

    brand_in = next(c["brand"]["$in"] for c in and_clauses if "brand" in c and "$in" in c["brand"])
    brand_nin = next(c["brand"]["$nin"] for c in and_clauses if "brand" in c and "$nin" in c["brand"])

    assert {"Sony", "SONY", "sony", "T-fal", "T-Fal", "T-FAL"}.issubset(set(brand_in))
    assert {"Apple", "APPLE", "apple"}.issubset(set(brand_nin))


def test_generalized_brand_matching_missing_brand_omits_filter():
    agent = object.__new__(RetrievalAgent)
    qu = QueryUnderstanding(
        raw_query="ergonomic office chair",
        intent=Intent.PRODUCT_RECOMMENDATION,
        category=RetailCategory.OFFICE_PRODUCTS,
        product_type="office chair",
        brand=BrandPreferences(preferred=[], excluded=[]),
    )
    where = agent._build_where_filter(qu)
    assert where == {"main_category": {"$eq": "Office_Products"}}


# ===================================================================
# 5. GEMINI FAILURE HANDLING & CONSERVATIVE QU FALLBACK
# ===================================================================

def test_gemini_retry_does_not_retry_auth_error():
    err = Exception("401 API_KEY_INVALID: Unauthorized")
    fn = MagicMock(side_effect=err)
    with pytest.raises(Exception, match="401"):
        call_with_retry(fn, context="test_auth")
    assert fn.call_count == 1


@patch("time.sleep")
def test_gemini_retry_handles_network_timeout(mock_sleep):
    fn = MagicMock(side_effect=[httpx.TimeoutException("timeout"), "ok"])
    assert call_with_retry(fn, context="test_net") == "ok"
    assert fn.call_count == 2


def test_qu_conservative_fallback_on_gemini_failure():
    agent = object.__new__(QueryUnderstandingAgent)
    agent.client = MagicMock()
    agent.client.models.generate_content.side_effect = Exception("403 PERMISSION_DENIED")

    qu = agent.understand("Sony headphones under ₹5000")
    assert qu.intent == Intent.PRODUCT_RECOMMENDATION
    assert qu.category == RetailCategory.ELECTRONICS
    assert qu.budget_max == 5000.0
    assert "Sony" in qu.brand.preferred


def test_qu_ambiguous_query_raises_on_gemini_failure():
    agent = object.__new__(QueryUnderstandingAgent)
    agent.client = MagicMock()
    agent.client.models.generate_content.side_effect = Exception("403 PERMISSION_DENIED")

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        agent.understand("something nice for my cousin")


# ===================================================================
# 6. LANGGRAPH STATE SERIALIZATION
# ===================================================================

def test_pipeline_state_has_no_future_key():
    assert "_fu_future" not in PipelineState.__annotations__

    qu = QueryUnderstanding(
        raw_query="best phone",
        intent=Intent.PRODUCT_RECOMMENDATION,
        category=RetailCategory.CELL_PHONES_AND_ACCESSORIES,
        product_type="phone",
        brand=BrandPreferences(),
    )
    from backend.Agents.retrieval.schema import RetrievalResult
    state: PipelineState = {
        "raw_query": "best phone",
        "user_id": "anonymous",
        "top_k": 3,
        "query_understanding": qu,
        "retrieval_result": RetrievalResult(
            query_text_used="best phone",
            hard_filters_applied={},
            hard_constraints_not_enforced=[],
            total_candidates_returned=0,
            products=[],
        ),
    }
    out = ranking_node(state)
    assert "_fu_future" not in out
    assert isinstance(out["ranking_result"], RankingResult)


# ===================================================================
# 7. BOUNDED PRODUCT IMAGE CACHE
# ===================================================================

def test_product_image_cache_is_bounded_lru(monkeypatch):
    monkeypatch.setattr(product_images, "_MAX_IMAGE_CACHE_SIZE", 3)
    with product_images._IMAGE_CACHE_LOCK:
        product_images._IMAGE_CACHE.clear()

    product_images._cache_set("A1", "https://example.com/1.jpg")
    product_images._cache_set("A2", "https://example.com/2.jpg")
    product_images._cache_set("A3", "https://example.com/3.jpg")
    # Access A1 so A2 becomes LRU
    hit, val = product_images._cache_get("A1")
    assert hit is True and val == "https://example.com/1.jpg"

    # Insert A4 -> evicts A2
    product_images._cache_set("A4", "https://example.com/4.jpg")
    with product_images._IMAGE_CACHE_LOCK:
        keys = list(product_images._IMAGE_CACHE.keys())
    assert len(keys) == 3
    assert "A2" not in keys
    assert set(keys) == {"A1", "A3", "A4"}
