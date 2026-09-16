"""App-shell tests: health endpoints, problem-details errors, request ids."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from polsia.api.main import create_app
from polsia.core.shared.errors import OrganizationNotFoundError, ValidationError
from polsia.core.shared.ids import OrganizationId
from polsia.infra.settings import Settings


def _make_app() -> object:
    app = create_app(Settings(env="test"))

    # Temporary test routes that raise domain errors.
    @app.get("/test/conflict")
    async def conflict() -> None:
        raise OrganizationNotFoundError(f"organization {OrganizationId('org_missing')} not found")

    @app.get("/test/validation")
    async def validation() -> None:
        raise ValidationError("bad input")

    return app


@pytest.fixture
async def client():
    app = _make_app()
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthEndpoints:
    async def test_healthz_always_ok(self, client: AsyncClient) -> None:
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_readyz_without_db_configured(self, client: AsyncClient) -> None:
        response = await client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "db": "not-configured"}


class TestProblemDetailsErrors:
    async def test_polsia_error_maps_to_problem_details(self, client: AsyncClient) -> None:
        response = await client.get("/test/conflict")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["status"] == 404
        assert body["code"] == "organization_not_found"
        assert body["title"] == "organization not found"
        assert body["type"].endswith("/organization_not_found")
        assert "org_missing" in body["detail"]

    async def test_validation_error_maps_to_422(self, client: AsyncClient) -> None:
        response = await client.get("/test/validation")
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"

    async def test_request_validation_returns_problem_shape(self, client: AsyncClient) -> None:
        # No POST route exists -> 404 problem shape (handler consistency check).
        response = await client.get("/definitely-not-a-route")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["code"] == "not_found"
        assert body["status"] == 404


class TestRequestContextMiddleware:
    async def test_echoes_incoming_request_id(self, client: AsyncClient) -> None:
        response = await client.get("/healthz", headers={"X-Request-ID": "my-trace-123"})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "my-trace-123"

    async def test_creates_request_id_when_missing(self, client: AsyncClient) -> None:
        response = await client.get("/healthz")
        request_id = response.headers.get("X-Request-ID")
        assert request_id
        assert request_id.startswith("req_")

    async def test_request_ids_differ_between_requests(self, client: AsyncClient) -> None:
        first = (await client.get("/healthz")).headers["X-Request-ID"]
        second = (await client.get("/healthz")).headers["X-Request-ID"]
        assert first != second
