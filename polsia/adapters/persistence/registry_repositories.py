"""SQLAlchemy implementations of the registry repository ports."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.registry_models import (
    AgentModel,
    ModelRegistryModel,
    SkillModel,
    ToolModel,
)
from polsia.adapters.persistence.translate import translate_integrity_error
from polsia.core.registry.entities import Agent, ModelEntry, Skill, Tool
from polsia.core.registry.values import RegistryStatus
from polsia.core.shared.errors import NotFoundError
from polsia.core.shared.ids import AgentId, CompanyId, ModelId, SkillId, ToolId


def _aware(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


# -- Agent (company-scoped) -----------------------------------------------------


def _to_agent(row: AgentModel) -> Agent:
    return Agent(
        id=AgentId.parse(row.id),
        company_id=CompanyId.parse(row.company_id),
        name=row.name,
        type=row.type,
        description=row.description,
        status=RegistryStatus(row.status),
        model_reference=row.model_reference,
        system_instructions=row.system_instructions,
        capabilities=list(row.capabilities_list or []),
        configuration=dict(row.configuration or {}),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


class SqlAlchemyAgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, agent: Agent) -> None:
        row = AgentModel(
            id=str(agent.id),
            company_id=str(agent.company_id),
            name=agent.name,
            type=agent.type,
            description=agent.description,
            status=agent.status.value,
            model_reference=agent.model_reference,
            system_instructions=agent.system_instructions,
            capabilities_list=list(agent.capabilities),
            configuration=dict(agent.configuration),
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, company_id: CompanyId, agent_id: AgentId) -> Agent | None:
        stmt = select(AgentModel).where(
            AgentModel.id == str(agent_id),
            AgentModel.company_id == str(company_id),
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_agent(row) if row is not None else None

    async def get_by_name(self, company_id: CompanyId, name: str) -> Agent | None:
        stmt = select(AgentModel).where(
            AgentModel.company_id == str(company_id),
            AgentModel.name == name,
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_agent(row) if row is not None else None

    async def list_for_company(self, company_id: CompanyId) -> list[Agent]:
        stmt = (
            select(AgentModel)
            .where(AgentModel.company_id == str(company_id))
            .order_by(AgentModel.created_at, AgentModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_agent(row) for row in rows]

    async def update(self, agent: Agent) -> None:
        row = await self._session.get(AgentModel, str(agent.id))
        if row is None or row.company_id != str(agent.company_id):
            raise NotFoundError(f"agent {agent.id} not found")
        row.name = agent.name
        row.type = agent.type
        row.description = agent.description
        row.status = agent.status.value
        row.model_reference = agent.model_reference
        row.system_instructions = agent.system_instructions
        row.capabilities_list = list(agent.capabilities)
        row.configuration = dict(agent.configuration)
        row.updated_at = agent.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc


# -- Skill / Tool / Model (platform-global, name@version) -----------------------


def _to_skill(row: SkillModel) -> Skill:
    return Skill(
        id=SkillId.parse(row.id),
        name=row.name,
        version=row.version,
        description=row.description,
        status=RegistryStatus(row.status),
        input_schema=dict(row.input_schema or {}),
        output_schema=dict(row.output_schema or {}),
        required_capabilities=list(row.required_capabilities or []),
        required_tools=list(row.required_tools or []),
        required_permissions=list(row.required_permissions or []),
        configuration=dict(row.configuration or {}),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _to_tool(row: ToolModel) -> Tool:
    return Tool(
        id=ToolId.parse(row.id),
        name=row.name,
        version=row.version,
        description=row.description,
        status=RegistryStatus(row.status),
        input_schema=dict(row.input_schema or {}),
        output_schema=dict(row.output_schema or {}),
        capabilities=list(row.capabilities_list or []),
        configuration=dict(row.configuration or {}),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _to_model_entry(row: ModelRegistryModel) -> ModelEntry:
    return ModelEntry(
        id=ModelId.parse(row.id),
        name=row.name,
        version=row.version,
        provider=row.provider,
        description=row.description,
        status=RegistryStatus(row.status),
        capabilities=list(row.capabilities_list or []),
        configuration=dict(row.configuration or {}),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


class SqlAlchemySkillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, skill: Skill) -> None:
        row = SkillModel(
            id=str(skill.id),
            name=skill.name,
            version=skill.version,
            description=skill.description,
            status=skill.status.value,
            input_schema=dict(skill.input_schema),
            output_schema=dict(skill.output_schema),
            required_capabilities=list(skill.required_capabilities),
            required_tools=list(skill.required_tools),
            required_permissions=list(skill.required_permissions),
            configuration=dict(skill.configuration),
            created_at=skill.created_at,
            updated_at=skill.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, skill_id: SkillId) -> Skill | None:
        row = await self._session.get(SkillModel, str(skill_id))
        return _to_skill(row) if row is not None else None

    async def get_by_key(self, name: str, version: str) -> Skill | None:
        stmt = select(SkillModel).where(SkillModel.name == name, SkillModel.version == version)
        row = (await self._session.scalars(stmt)).first()
        return _to_skill(row) if row is not None else None

    async def list_all(self) -> list[Skill]:
        stmt = select(SkillModel).order_by(SkillModel.name, SkillModel.version)
        rows = (await self._session.scalars(stmt)).all()
        return [_to_skill(row) for row in rows]

    async def list_by_name(self, name: str) -> list[Skill]:
        stmt = select(SkillModel).where(SkillModel.name == name).order_by(SkillModel.version)
        rows = (await self._session.scalars(stmt)).all()
        return [_to_skill(row) for row in rows]

    async def update(self, skill: Skill) -> None:
        row = await self._session.get(SkillModel, str(skill.id))
        if row is None:
            raise NotFoundError(f"skill {skill.id} not found")
        row.description = skill.description
        row.status = skill.status.value
        row.input_schema = dict(skill.input_schema)
        row.output_schema = dict(skill.output_schema)
        row.required_capabilities = list(skill.required_capabilities)
        row.required_tools = list(skill.required_tools)
        row.required_permissions = list(skill.required_permissions)
        row.configuration = dict(skill.configuration)
        row.updated_at = skill.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc


class SqlAlchemyToolRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tool: Tool) -> None:
        row = ToolModel(
            id=str(tool.id),
            name=tool.name,
            version=tool.version,
            description=tool.description,
            status=tool.status.value,
            input_schema=dict(tool.input_schema),
            output_schema=dict(tool.output_schema),
            capabilities_list=list(tool.capabilities),
            configuration=dict(tool.configuration),
            created_at=tool.created_at,
            updated_at=tool.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, tool_id: ToolId) -> Tool | None:
        row = await self._session.get(ToolModel, str(tool_id))
        return _to_tool(row) if row is not None else None

    async def get_by_key(self, name: str, version: str) -> Tool | None:
        stmt = select(ToolModel).where(ToolModel.name == name, ToolModel.version == version)
        row = (await self._session.scalars(stmt)).first()
        return _to_tool(row) if row is not None else None

    async def list_all(self) -> list[Tool]:
        stmt = select(ToolModel).order_by(ToolModel.name, ToolModel.version)
        rows = (await self._session.scalars(stmt)).all()
        return [_to_tool(row) for row in rows]

    async def list_by_name(self, name: str) -> list[Tool]:
        stmt = select(ToolModel).where(ToolModel.name == name).order_by(ToolModel.version)
        rows = (await self._session.scalars(stmt)).all()
        return [_to_tool(row) for row in rows]

    async def update(self, tool: Tool) -> None:
        row = await self._session.get(ToolModel, str(tool.id))
        if row is None:
            raise NotFoundError(f"tool {tool.id} not found")
        row.description = tool.description
        row.status = tool.status.value
        row.input_schema = dict(tool.input_schema)
        row.output_schema = dict(tool.output_schema)
        row.capabilities_list = list(tool.capabilities)
        row.configuration = dict(tool.configuration)
        row.updated_at = tool.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc


class SqlAlchemyModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, model: ModelEntry) -> None:
        row = ModelRegistryModel(
            id=str(model.id),
            name=model.name,
            version=model.version,
            provider=model.provider,
            description=model.description,
            status=model.status.value,
            capabilities_list=list(model.capabilities),
            configuration=dict(model.configuration),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, model_id: ModelId) -> ModelEntry | None:
        row = await self._session.get(ModelRegistryModel, str(model_id))
        return _to_model_entry(row) if row is not None else None

    async def get_by_key(self, name: str, version: str) -> ModelEntry | None:
        stmt = select(ModelRegistryModel).where(
            ModelRegistryModel.name == name, ModelRegistryModel.version == version
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_model_entry(row) if row is not None else None

    async def list_all(self) -> list[ModelEntry]:
        stmt = select(ModelRegistryModel).order_by(
            ModelRegistryModel.name, ModelRegistryModel.version
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_model_entry(row) for row in rows]

    async def list_by_name(self, name: str) -> list[ModelEntry]:
        stmt = (
            select(ModelRegistryModel)
            .where(ModelRegistryModel.name == name)
            .order_by(ModelRegistryModel.version)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_model_entry(row) for row in rows]

    async def update(self, model: ModelEntry) -> None:
        row = await self._session.get(ModelRegistryModel, str(model.id))
        if row is None:
            raise NotFoundError(f"model {model.id} not found")
        row.description = model.description
        row.provider = model.provider
        row.status = model.status.value
        row.capabilities_list = list(model.capabilities)
        row.configuration = dict(model.configuration)
        row.updated_at = model.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc
