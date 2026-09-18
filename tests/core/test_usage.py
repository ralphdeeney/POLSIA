"""Usage endpoint tests (Phase 2 slice 8, company-scoped, tenant-isolated)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from polsia.adapters.cost import NullCostCalculator
from polsia.api.main import create_app
from polsia.core.cost.ports import UsageRecord
from polsia.core.runtime.ports import RuntimeResult, TokenUsage
from polsia.core.shared.ids import AgentId, CompanyId, TaskExecutionId, TaskId
from polsia.infra.db import create_all_metadata
from polsia.infra.settings import Settings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

PASSWORD = "super-secret-1"


@pytest.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        app = create_app(Settings(env="test", jwt_secret="test-secret"), session_factory=factory)
        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        await engine.dispose()


async def register(client: AsyncClient, email: str) -> dict[str, Any]:
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(user: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['tokens']['access_token']}"}


async def make_company(
    client: AsyncClient, email: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    user = await register(client, email)
    response = await client.post(
        "/organizations",
        json={"name": f"Org of {email}", "slug": email.split("@")[0]},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    org = response.json()
    response = await client.post(
        f"/organizations/{org['id']}/companies",
        json={"name": "Usage Co", "slug": "usage-co"},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return user, org, response.json()


def usage_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/usage"


class TestUsageRecordEntity:
    def test_create_and_counts(self) -> None:
        record = UsageRecord.create(
            company_id=CompanyId.generate(),
            execution_id=TaskExecutionId.generate(),
            task_id=TaskId.generate(),
            agent_id=AgentId.generate(),
            runtime="mock",
            model="generic-llm@1.0.0",
            prompt_tokens=120,
            completion_tokens=30,
            tool_calls=2,
            duration_seconds=1.5,
        )
        assert record.prompt_tokens + record.completion_tokens == 150
        assert record.tool_calls == 2
        assert record.estimated_cost is None

    def test_negative_counts_rejected(self) -> None:
        with pytest.raises(Exception):
            UsageRecord.create(
                company_id=CompanyId.generate(),
                execution_id=TaskExecutionId.generate(),
                task_id=TaskId.generate(),
                prompt_tokens=-1,
            )

    def test_from_runtime_result(self) -> None:
        result = RuntimeResult.completed(
            {"ok": True},
            runtime="mock",
            model="generic-llm@1.0.0",
            token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            tool_calls=[],
            duration_seconds=0.25,
        )
        record = UsageRecord.from_runtime_result(
            company_id=CompanyId.generate(),
            execution_id=TaskExecutionId.generate(),
            task_id=TaskId.generate(),
            agent_id=None,
            runtime="mock",
            model="generic-llm@1.0.0",
            usage=result.token_usage,
            tool_call_count=len(result.tool_calls),
            duration_seconds=result.duration_seconds,
        )
        assert record.prompt_tokens == 10
        assert record.completion_tokens == 5
        assert record.duration_seconds == 0.25


class TestNullCostCalculator:
    def test_never_prices(self) -> None:
        record = UsageRecord.create(
            company_id=CompanyId.generate(),
            execution_id=TaskExecutionId.generate(),
            task_id=TaskId.generate(),
        )
        assert NullCostCalculator().estimate(record) is None
