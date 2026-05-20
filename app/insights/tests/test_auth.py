"""Unit + integration tests for the X-API-Key auth dependency.

Exercises both the dependency behaviour (`verify_api_key`) and the
CSV-string-to-list parser on `InsightSettings.api_keys` (which is what
pydantic-settings sees when the env var is supplied).

The tests build a tiny FastAPI app per test rather than mounting the
real Insight router — that keeps the assertions about HTTP status codes
focused on auth alone and avoids dragging in the entire app surface.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.insights.config.settings import InsightSettings, get_settings
from app.security import API_KEY_HEADER, verify_api_key

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_settings(**overrides) -> InsightSettings:
    """Build an InsightSettings instance with `.env` loading disabled.

    Without `_env_file=None`, pydantic-settings would happily read the
    developer's local `.env`, contaminating the test's view of the world.
    """
    return InsightSettings(_env_file=None, **overrides)


def _build_client(settings: InsightSettings) -> Iterator[TestClient]:
    """Yield a TestClient bound to an app with one protected route."""
    router = APIRouter(dependencies=[Depends(verify_api_key)])

    @router.get("/protected")
    def _protected() -> dict:
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def protected_client_with_keys() -> Iterator[TestClient]:
    settings = _make_settings(environment="development", api_keys=["valid-key"])
    yield from _build_client(settings)


@pytest.fixture
def protected_client_multi_keys() -> Iterator[TestClient]:
    settings = _make_settings(environment="development", api_keys=["a", "b", "c"])
    yield from _build_client(settings)


@pytest.fixture
def protected_client_dev_no_keys() -> Iterator[TestClient]:
    settings = _make_settings(environment="development", api_keys=[])
    yield from _build_client(settings)


@pytest.fixture
def protected_client_prod_no_keys() -> Iterator[TestClient]:
    settings = _make_settings(environment="production", api_keys=[])
    yield from _build_client(settings)


# --------------------------------------------------------------------------- #
# Dependency behaviour                                                        #
# --------------------------------------------------------------------------- #


def test_missing_header_returns_401(protected_client_with_keys: TestClient) -> None:
    response = protected_client_with_keys.get("/protected")
    assert response.status_code == 401
    assert API_KEY_HEADER in response.json()["detail"]
    # 401 should advertise the auth scheme so generic clients can react.
    assert response.headers.get("www-authenticate") == API_KEY_HEADER


def test_wrong_key_returns_403(protected_client_with_keys: TestClient) -> None:
    response = protected_client_with_keys.get(
        "/protected",
        headers={API_KEY_HEADER: "definitely-wrong"},
    )
    assert response.status_code == 403
    assert "Invalid" in response.json()["detail"]


def test_valid_key_returns_200(protected_client_with_keys: TestClient) -> None:
    response = protected_client_with_keys.get(
        "/protected",
        headers={API_KEY_HEADER: "valid-key"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_any_configured_key_is_accepted(
    protected_client_multi_keys: TestClient,
) -> None:
    for key in ("a", "b", "c"):
        response = protected_client_multi_keys.get(
            "/protected",
            headers={API_KEY_HEADER: key},
        )
        assert response.status_code == 200, key


def test_dev_env_with_no_keys_allows_unauthenticated_requests(
    protected_client_dev_no_keys: TestClient,
) -> None:
    """Local development must remain friction-free when no keys are set."""
    response = protected_client_dev_no_keys.get("/protected")
    assert response.status_code == 200


def test_prod_env_with_no_keys_returns_503(
    protected_client_prod_no_keys: TestClient,
) -> None:
    """Production without configured keys must fail closed, not silently open."""
    response = protected_client_prod_no_keys.get("/protected")
    assert response.status_code == 503
    assert "Authentication is not configured" in response.json()["detail"]


def test_prod_env_with_keys_enforces_auth() -> None:
    """Sanity: production + configured keys behaves like dev + configured keys."""
    settings = _make_settings(environment="production", api_keys=["prod-key"])
    client_iter = _build_client(settings)
    client = next(client_iter)
    try:
        # Missing header
        assert client.get("/protected").status_code == 401
        # Wrong key
        assert client.get("/protected", headers={API_KEY_HEADER: "no"}).status_code == 403
        # Right key
        assert client.get("/protected", headers={API_KEY_HEADER: "prod-key"}).status_code == 200
    finally:
        # Drain the generator so the cleanup in _build_client runs.
        for _ in client_iter:
            pass


# --------------------------------------------------------------------------- #
# CSV-string parsing for VOICEIQ_API_KEYS                                     #
# --------------------------------------------------------------------------- #


def test_csv_env_value_parses_into_list() -> None:
    settings = _make_settings(api_keys="k1,k2,k3")  # type: ignore[arg-type]
    assert settings.api_keys == ["k1", "k2", "k3"]


def test_csv_env_value_trims_whitespace() -> None:
    settings = _make_settings(api_keys="  k1 , k2  ,  k3 ")  # type: ignore[arg-type]
    assert settings.api_keys == ["k1", "k2", "k3"]


def test_csv_env_value_drops_empty_entries() -> None:
    """Trailing or consecutive commas must not yield empty-string keys.

    Empty-string keys would otherwise be a footgun: every request with
    no header would match an empty configured key via compare_digest.
    """
    settings = _make_settings(api_keys=",,k1,,,k2,")  # type: ignore[arg-type]
    assert settings.api_keys == ["k1", "k2"]


def test_list_value_passes_through_unchanged() -> None:
    """The validator must be a no-op when the value is already a list."""
    settings = _make_settings(api_keys=["already", "a", "list"])
    assert settings.api_keys == ["already", "a", "list"]
