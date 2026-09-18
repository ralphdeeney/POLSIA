"""Registry application services (Phase 2, decision C2).

- AgentService: company-scoped (org membership + company scope required).
- SkillService / ToolService / ModelService: platform-global registration
  and discovery. NOTE (Phase 2 assumption): registration is available to any
  authenticated user; operator-level administration is a later phase. Global
  availability is separate from authorization — a registered capability does
  not imply any company or agent may use it (PermissionEngine, Slice 4).
"""

from __future__ import annotations

from typing import Any, TypeVar

from polsia.core.registry.entities import Agent, ModelEntry, Skill, Tool
from polsia.core.registry.ports import (
    AgentRepository,
    ModelRepository,
    SkillRepository,
    ToolRepository,
)
from polsia.core.registry.values import RegistryStatus
from polsia.core.shared.errors import (
    ConflictError,
    NotAMember,
    NotFoundError,
    OrganizationNotFoundError,
)
from polsia.core.shared.events import EventBus, EventEnvelope, EventStore, emit
from polsia.core.shared.ids import AgentId, CompanyId, ModelId, OrganizationId, SkillId, ToolId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository

T = TypeVar("T")


class AgentService:
    """Company-scoped agent registry use cases."""

    def __init__(
        self,
        agents: AgentRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        store: EventStore | None = None,
    ) -> None:
        self._agents = agents
        self._companies = companies
        self._memberships = memberships
        self._bus = bus
        self._store = store

    async def register_agent(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        name: str,
        type: str = "generic",  # noqa: A002
        description: str | None = None,
        model_reference: str | None = None,
        system_instructions: str | None = None,
        capabilities: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> Agent:
        await self._require_company(actor, organization_id, company_id)
        if await self._agents.get_by_name(company_id, name) is not None:
            raise ConflictError(f"agent {name!r} already exists in company {company_id}")
        agent = Agent.create(
            company_id=company_id,
            name=name,
            type=type,
            description=description,
            model_reference=model_reference,
            system_instructions=system_instructions,
            capabilities=capabilities,
            configuration=configuration,
        )
        await self._agents.add(agent)
        await self._publish(
            "agent.registered",
            organization_id,
            company_id,
            {"agent_id": str(agent.id), "name": agent.name, "type": agent.type},
        )
        return agent

    async def get_agent(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId, agent_id: AgentId
    ) -> Agent:
        await self._require_company(actor, organization_id, company_id)
        return await self._load(company_id, agent_id)

    async def list_agents(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId
    ) -> list[Agent]:
        await self._require_company(actor, organization_id, company_id)
        return await self._agents.list_for_company(company_id)

    async def update_agent(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        agent_id: AgentId,
        *,
        name: str | None = None,
        type: str | None = None,  # noqa: A002
        description: str | None = None,
        model_reference: str | None = None,
        system_instructions: str | None = None,
        capabilities: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> Agent:
        await self._require_company(actor, organization_id, company_id)
        agent = await self._load(company_id, agent_id)
        agent = agent.update(
            name=name,
            type=type,
            description=description,
            model_reference=model_reference,
            system_instructions=system_instructions,
            capabilities=capabilities,
            configuration=configuration,
        )
        await self._agents.update(agent)
        await self._publish(
            "agent.updated",
            organization_id,
            company_id,
            {"agent_id": str(agent.id), "name": agent.name, "status": agent.status.value},
        )
        return agent

    async def set_agent_status(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        agent_id: AgentId,
        status: RegistryStatus,  # noqa: A002
    ) -> Agent:
        await self._require_company(actor, organization_id, company_id)
        agent = await self._load(company_id, agent_id)
        agent = agent.disable() if status is RegistryStatus.DISABLED else agent.enable()
        await self._agents.update(agent)
        await self._publish(
            "agent.updated",
            organization_id,
            company_id,
            {"agent_id": str(agent.id), "name": agent.name, "status": agent.status.value},
        )
        return agent

    async def _require_company(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId
    ) -> None:
        if await self._memberships.get(organization_id, actor.id) is None:
            raise NotAMember(f"user {actor.id} is not a member of organization {organization_id}")
        company = await self._companies.get(organization_id, company_id)
        if company is None:
            raise OrganizationNotFoundError(
                f"company {company_id} not found in organization {organization_id}"
            )

    async def _load(self, company_id: CompanyId, agent_id: AgentId) -> Agent:
        agent = await self._agents.get(company_id, agent_id)
        if agent is None:
            raise NotFoundError(f"agent {agent_id} not found")
        return agent

    async def _publish(
        self,
        event_type: str,
        organization_id: OrganizationId,
        company_id: CompanyId,
        payload: dict[str, Any],
    ) -> None:
        await emit(
            self._bus,
            self._store,
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload=payload,
            ),
        )


class _GlobalRegistryService:
    """Shared machinery for platform-global, versioned registries."""

    entity_name: str
    event_namespace: str

    def __init__(self, bus: EventBus, store: EventStore | None = None) -> None:
        self._bus = bus
        self._store = store

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        await emit(
            self._bus,
            self._store,
            EventEnvelope(event_type=f"{self.event_namespace}.{event_type}", payload=payload),
        )

    def _check_duplicate(self, existing: Any) -> None:
        if existing is not None:
            raise ConflictError(
                f"{self.entity_name} {existing.name}@{existing.version} already registered"
            )

    def _load_or_404(self, existing: T | None, identifier: str) -> T:
        if existing is None:
            raise NotFoundError(f"{self.entity_name} {identifier} not found")
        return existing


class SkillService(_GlobalRegistryService):
    entity_name = "skill"
    event_namespace = "skill"

    def __init__(
        self, skills: SkillRepository, bus: EventBus, store: EventStore | None = None
    ) -> None:
        super().__init__(bus, store)
        self._skills = skills

    async def register_skill(self, *, name: str, version: str, **kwargs: Any) -> Skill:
        self._check_duplicate(await self._skills.get_by_key(name, version))
        skill = Skill.create(name=name, version=version, **kwargs)
        await self._skills.add(skill)
        await self._publish(
            "registered",
            {"skill_id": str(skill.id), "name": skill.name, "version": skill.version},
        )
        return skill

    async def get_skill(self, skill_id: SkillId) -> Skill:
        return self._load_or_404(await self._skills.get(skill_id), str(skill_id))

    async def get_skill_by_key(self, name: str, version: str) -> Skill:
        return self._load_or_404(await self._skills.get_by_key(name, version), f"{name}@{version}")

    async def list_skills(self) -> list[Skill]:
        return await self._skills.list_all()

    async def list_skill_versions(self, name: str) -> list[Skill]:
        return await self._skills.list_by_name(name)

    async def update_skill(self, skill_id: SkillId, **kwargs: Any) -> Skill:
        skill = await self.get_skill(skill_id)
        skill = skill.update(**kwargs)
        await self._skills.update(skill)
        await self._publish(
            "updated",
            {"skill_id": str(skill.id), "name": skill.name, "version": skill.version},
        )
        return skill

    async def set_skill_status(self, skill_id: SkillId, status: RegistryStatus) -> Skill:  # noqa: A002
        skill = await self.get_skill(skill_id)
        skill = skill.disable() if status is RegistryStatus.DISABLED else skill.enable()
        await self._skills.update(skill)
        await self._publish(
            "updated",
            {
                "skill_id": str(skill.id),
                "name": skill.name,
                "version": skill.version,
                "status": skill.status.value,
            },
        )
        return skill


class ToolService(_GlobalRegistryService):
    entity_name = "tool"
    event_namespace = "tool"

    def __init__(
        self, tools: ToolRepository, bus: EventBus, store: EventStore | None = None
    ) -> None:
        super().__init__(bus, store)
        self._tools = tools

    async def register_tool(self, *, name: str, version: str, **kwargs: Any) -> Tool:
        self._check_duplicate(await self._tools.get_by_key(name, version))
        tool = Tool.create(name=name, version=version, **kwargs)
        await self._tools.add(tool)
        await self._publish(
            "registered", {"tool_id": str(tool.id), "name": tool.name, "version": tool.version}
        )
        return tool

    async def get_tool(self, tool_id: ToolId) -> Tool:
        return self._load_or_404(await self._tools.get(tool_id), str(tool_id))

    async def get_tool_by_key(self, name: str, version: str) -> Tool:
        return self._load_or_404(await self._tools.get_by_key(name, version), f"{name}@{version}")

    async def list_tools(self) -> list[Tool]:
        return await self._tools.list_all()

    async def list_tool_versions(self, name: str) -> list[Tool]:
        return await self._tools.list_by_name(name)

    async def update_tool(self, tool_id: ToolId, **kwargs: Any) -> Tool:
        tool = await self.get_tool(tool_id)
        tool = tool.update(**kwargs)
        await self._tools.update(tool)
        await self._publish(
            "updated", {"tool_id": str(tool.id), "name": tool.name, "version": tool.version}
        )
        return tool

    async def set_tool_status(self, tool_id: ToolId, status: RegistryStatus) -> Tool:  # noqa: A002
        tool = await self.get_tool(tool_id)
        tool = tool.disable() if status is RegistryStatus.DISABLED else tool.enable()
        await self._tools.update(tool)
        await self._publish(
            "updated",
            {
                "tool_id": str(tool.id),
                "name": tool.name,
                "version": tool.version,
                "status": tool.status.value,
            },
        )
        return tool


class ModelService(_GlobalRegistryService):
    entity_name = "model"
    event_namespace = "model"

    def __init__(
        self, models: ModelRepository, bus: EventBus, store: EventStore | None = None
    ) -> None:
        super().__init__(bus, store)
        self._models = models

    async def register_model(self, *, name: str, version: str, **kwargs: Any) -> ModelEntry:
        self._check_duplicate(await self._models.get_by_key(name, version))
        model = ModelEntry.create(name=name, version=version, **kwargs)
        await self._models.add(model)
        await self._publish(
            "registered", {"model_id": str(model.id), "name": model.name, "version": model.version}
        )
        return model

    async def get_model(self, model_id: ModelId) -> ModelEntry:
        return self._load_or_404(await self._models.get(model_id), str(model_id))

    async def get_model_by_key(self, name: str, version: str) -> ModelEntry:
        return self._load_or_404(await self._models.get_by_key(name, version), f"{name}@{version}")

    async def list_models(self) -> list[ModelEntry]:
        return await self._models.list_all()

    async def list_model_versions(self, name: str) -> list[ModelEntry]:
        return await self._models.list_by_name(name)

    async def update_model(self, model_id: ModelId, **kwargs: Any) -> ModelEntry:
        model = await self.get_model(model_id)
        model = model.update(**kwargs)
        await self._models.update(model)
        await self._publish(
            "updated", {"model_id": str(model.id), "name": model.name, "version": model.version}
        )
        return model

    async def set_model_status(self, model_id: ModelId, status: RegistryStatus) -> ModelEntry:  # noqa: A002
        model = await self.get_model(model_id)
        model = model.disable() if status is RegistryStatus.DISABLED else model.enable()
        await self._models.update(model)
        await self._publish(
            "updated",
            {
                "model_id": str(model.id),
                "name": model.name,
                "version": model.version,
                "status": model.status.value,
            },
        )
        return model
