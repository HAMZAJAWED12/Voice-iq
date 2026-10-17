"""Deployment defaults that keep the service closed unless configured.

Staging, like production, refuses every protected request when no API key
is configured; the compose file publishes the port on the loopback
interface only.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.insights.config.settings import InsightSettings, get_settings
from app.security.api_key import verify_api_key

REPO = Path(__file__).resolve().parents[3]


def _client(environment: str) -> Iterator[TestClient]:
    router = APIRouter(dependencies=[Depends(verify_api_key)])

    @router.get("/protected")
    def _protected() -> dict:
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    settings = InsightSettings(_env_file=None, environment=environment, api_keys=[])
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_staging_and_production_fail_closed_without_keys(environment):
    client = next(_client(environment))

    assert client.get("/protected").status_code == 503


@pytest.mark.parametrize("environment", ["development", "test"])
def test_development_and_test_still_allow_requests_without_keys(environment):
    client = next(_client(environment))

    assert client.get("/protected").status_code == 200


def test_compose_publishes_ports_on_loopback_only():
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    block = re.search(r"^\s*ports:\s*\n((?:\s*-\s*.+\n)+)", text, re.M)
    assert block, "no ports block"
    published = re.findall(r"-\s*\"?([^\"\n]+)\"?", block.group(1))

    assert published and all(p.startswith("127.0.0.1:") for p in published), published
