"""Registry repository tests (SQLite): CRUD, duplicates, update round-trips."""

from __future__ import annotations

from typing import Any

import pytest
from polsia.adapters.persistence.registry_repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyModelRepository,
    SqlAlchemySkillRepository,
    SqlAlchemyToolRepository,
)
from polsia.core.registry.entities import Agent, ModelEntry, Skill, Tool
from polsia.core.registry.values import RegistryStatus
from polsia.core.shared.errors import ConflictError, NotFoundError
from polsia.core.shared.ids import AgentId, CompanyId, ModelId, SkillId, ToolId
from polsia.infra.db import create_all_metadata
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def store_env():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            yield session, factory
    finally:
        await engine.dispose()


def make_agent(company_id: CompanyId, name: str = "worker") -> Agent:
    return Agent.create(company_id=company_id, name=name, type="generic", capabilities=["web"])


def make_skill(name: str = "research", version: str = "1.0.0") -> Skill:
    return Skill.create(
        name=name,
        version=version,
        required_tools=["web_search@1.0.0"],
        required_permissions=["web.search"],
        input_schema={"type": "object"},
    )


def make_tool(name: str = "web_search", version: str = "1.0.0") -> Tool:
    return Tool.create(name=name, version=version, capabilities=["web"])


def make_model(name: str = "generic-llm", version: str = "1.0.0") -> ModelEntry:
    return ModelEntry.create(name=name, version=version, provider="generic")


class TestAgentRepository:
    async def test_add_get_get_by_name_list(self, store_env: Any) -> None:
        session, _factory = store_env
        repo = SqlAlchemyAgentRepository(session)
        company = CompanyId.generate()
        agent = make_agent(company)
        await repo.add(agent)
        loaded = await repo.get(company, agent.id)
        assert loaded is not None
        assert loaded.name == "worker"
        assert loaded.capabilities == ["web"]
        assert await repo.get_by_name(company, "worker") is not None
        assert await repo.get_by_name(CompanyId.generate(), "worker") is None
        assert len(await repo.list_for_company(company)) == 1

    async def test_update_roundtrip(self, store_env: Any) -> None:
        session, _factory = store_env
        repo = SqlAlchemyAgentRepository(session)
        company = CompanyId.generate()
        agent = make_agent(company)
        await repo.add(agent)
        updated = agent.update(
            name="renamed",
            type="specialist",
            system_instructions="be terse",
            model_reference="generic-llm@1.0.0",
            capabilities=["shell"],
            configuration={"k": "v"},
        ).disable()
        await repo.update(updated)
        loaded = await repo.get(company, agent.id)
        assert loaded is not None
        assert loaded.name == "renamed"
        assert loaded.type == "specialist"
        assert loaded.status is RegistryStatus.DISABLED
        assert loaded.configuration == {"k": "v"}

    async def test_update_wrong_company_is_not_found(self, store_env: Any) -> None:
        session, _factory = store_env
        repo = SqlAlchemyAgentRepository(session)
        agent = make_agent(CompanyId.generate())
        await repo.add(agent)
        moved = make_agent(CompanyId.generate(), name="other")
        with pytest.raises(NotFoundError):
            await repo.update(moved)

    async def test_duplicate_name_conflict(self, store_env: Any) -> None:
        session, _factory = store_env
        repo = SqlAlchemyAgentRepository(session)
        company = CompanyId.generate()
        await repo.add(make_agent(company))
        with pytest.raises(ConflictError):  # unique violation translated
            await repo.add(make_agent(company))


class TestGlobalRegistries:
    async def test_skill_crud_and_versions(self, store_env: Any) -> None:
        session, _factory = store_env
        repo = SqlAlchemySkillRepository(session)
        await repo.add(make_skill())
        await repo.add(make_skill(version="1.1.0"))
        by_key = await repo.get_by_key("research", "1.0.0")
        assert by_key is not None
        assert by_key.required_tools == ["web_search@1.0.0"]
        assert len(await repo.list_by_name("research")) == 2
        assert await repo.get_by_key("research", "9.9.9") is None
        updated = by_key.update(description="updated", required_permissions=[])
        await repo.update(updated)
        loaded = await repo.get(by_key.id)
        assert loaded is not None
        assert loaded.description == "updated"
        with pytest.raises(ConflictError):
            await repo.add(make_skill())  # same name@version

    async def test_tool_crud(self, store_env: Any) -> None:
        session, _factory = store_env
        repo = SqlAlchemyToolRepository(session)
        tool = make_tool()
        await repo.add(tool)
        loaded = await repo.get(tool.id)
        assert loaded is not None
        assert loaded.capabilities == ["web"]
        assert await repo.get_by_key("web_search", "1.0.0") is not None
        updated = tool.update(description="patched").disable()
        await repo.update(updated)
        loaded = await repo.get(tool.id)
        assert loaded is not None
        assert loaded.status is RegistryStatus.DISABLED

    async def test_model_crud(self, store_env: Any) -> None:
        session, _factory = store_env
        repo = SqlAlchemyModelRepository(session)
        model = make_model()
        await repo.add(model)
        loaded = await repo.get(model.id)
        assert loaded is not None
        assert loaded.provider == "generic"
        assert await repo.get_by_key("generic-llm", "1.0.0") is not None
        updated = model.update(provider="other", description="changed")
        await repo.update(updated)
        loaded = await repo.get(model.id)
        assert loaded is not None
        assert loaded.provider == "other"

    async def test_update_missing_is_not_found(self, store_env: Any) -> None:
        session, _factory = store_env
        with pytest.raises(NotFoundError):
            await SqlAlchemySkillRepository(session).update(make_skill())
        with pytest.raises(NotFoundError):
            await SqlAlchemyToolRepository(session).update(make_tool())
        with pytest.raises(NotFoundError):
            await SqlAlchemyModelRepository(session).update(make_model())

    async def test_typed_ids_roundtrip(self, store_env: Any) -> None:
        session, _factory = store_env
        skill = make_skill()
        await SqlAlchemySkillRepository(session).add(skill)
        loaded = await SqlAlchemySkillRepository(session).get(skill.id)
        assert loaded is not None
        assert isinstance(loaded.id, SkillId)
        tool = make_tool()
        await SqlAlchemyToolRepository(session).add(tool)
        tool_loaded = await SqlAlchemyToolRepository(session).get(tool.id)
        assert tool_loaded is not None
        assert isinstance(tool_loaded.id, ToolId)
        model = make_model()
        await SqlAlchemyModelRepository(session).add(model)
        model_loaded = await SqlAlchemyModelRepository(session).get(model.id)
        assert model_loaded is not None
        assert isinstance(model_loaded.id, ModelId)
        agent = make_agent(CompanyId.generate())
        await SqlAlchemyAgentRepository(session).add(agent)
        agent_loaded = await SqlAlchemyAgentRepository(session).get(agent.company_id, agent.id)
        assert agent_loaded is not None
        assert isinstance(agent_loaded.id, AgentId)
