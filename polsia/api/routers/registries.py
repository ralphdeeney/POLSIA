"""Registry endpoints (Phase 2, decision C2).

- Agents: company-scoped under /organizations/{org_id}/companies/{company_id}.
- Skills / Tools / Models: platform-global (/skills, /tools, /models) with
  name@version identity. Global availability != authorization (Slice 4).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from polsia.api.deps import (
    AgentServiceDep,
    CurrentUser,
    ModelServiceDep,
    SkillServiceDep,
    ToolServiceDep,
    parse_agent_id,
    parse_company_id,
    parse_organization_id,
)
from polsia.api.schemas import (
    AgentCreate,
    AgentOut,
    AgentStatusChange,
    AgentUpdate,
    ModelCreate,
    ModelOut,
    ModelStatusChange,
    ModelUpdate,
    SkillCreate,
    SkillOut,
    SkillStatusChange,
    SkillUpdate,
    ToolCreate,
    ToolOut,
    ToolStatusChange,
    ToolUpdate,
)
from polsia.core.shared.ids import ModelId, SkillId, ToolId

agents_router = APIRouter(
    prefix="/organizations/{org_id}/companies/{company_id}/agents", tags=["agents"]
)
skills_router = APIRouter(prefix="/skills", tags=["skills"])
tools_router = APIRouter(prefix="/tools", tags=["tools"])
models_router = APIRouter(prefix="/models", tags=["models"])


# -- Agents (company-scoped) ----------------------------------------------------


@agents_router.post("", status_code=status.HTTP_201_CREATED, response_model=AgentOut)
async def register_agent(
    org_id: str, company_id: str, payload: AgentCreate, user: CurrentUser, service: AgentServiceDep
) -> AgentOut:
    agent = await service.register_agent(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        name=payload.name,
        type=payload.type,
        description=payload.description,
        model_reference=payload.model_reference,
        system_instructions=payload.system_instructions,
        capabilities=payload.capabilities,
        configuration=payload.configuration,
    )
    return AgentOut.model_validate(agent)


@agents_router.get("", response_model=list[AgentOut])
async def list_agents(
    org_id: str, company_id: str, user: CurrentUser, service: AgentServiceDep
) -> list[AgentOut]:
    agents = await service.list_agents(
        user, parse_organization_id(org_id), parse_company_id(company_id)
    )
    return [AgentOut.model_validate(a) for a in agents]


@agents_router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    org_id: str, company_id: str, agent_id: str, user: CurrentUser, service: AgentServiceDep
) -> AgentOut:
    agent = await service.get_agent(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        parse_agent_id(agent_id),
    )
    return AgentOut.model_validate(agent)


@agents_router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    org_id: str,
    company_id: str,
    agent_id: str,
    payload: AgentUpdate,
    user: CurrentUser,
    service: AgentServiceDep,
) -> AgentOut:
    updates = payload.model_dump(exclude_unset=True)
    agent = await service.update_agent(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        parse_agent_id(agent_id),
        name=updates.get("name"),
        type=updates.get("type"),
        description=updates.get("description"),
        model_reference=updates.get("model_reference"),
        system_instructions=updates.get("system_instructions"),
        capabilities=updates.get("capabilities"),
        configuration=updates.get("configuration"),
    )
    return AgentOut.model_validate(agent)


@agents_router.post("/{agent_id}/status", response_model=AgentOut)
async def change_agent_status(
    org_id: str,
    company_id: str,
    agent_id: str,
    payload: AgentStatusChange,
    user: CurrentUser,
    service: AgentServiceDep,
) -> AgentOut:
    agent = await service.set_agent_status(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        parse_agent_id(agent_id),
        payload.status,
    )
    return AgentOut.model_validate(agent)


# -- Skills / Tools / Models (platform-global) ----------------------------------


@skills_router.post("", status_code=status.HTTP_201_CREATED, response_model=SkillOut)
async def register_skill(
    payload: SkillCreate, user: CurrentUser, service: SkillServiceDep
) -> SkillOut:
    skill = await service.register_skill(
        name=payload.name,
        version=payload.version,
        description=payload.description,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        required_capabilities=payload.required_capabilities,
        required_tools=payload.required_tools,
        required_permissions=payload.required_permissions,
        configuration=payload.configuration,
    )
    return SkillOut.model_validate(skill)


@skills_router.get("", response_model=list[SkillOut])
async def list_skills(
    user: CurrentUser, service: SkillServiceDep, name: str | None = Query(default=None)
) -> list[SkillOut]:
    if name:
        skills = await service.list_skill_versions(name)
    else:
        skills = await service.list_skills()
    return [SkillOut.model_validate(s) for s in skills]


@skills_router.get("/{skill_id}", response_model=SkillOut)
async def get_skill(skill_id: str, user: CurrentUser, service: SkillServiceDep) -> SkillOut:
    skill = await service.get_skill(SkillId.parse(skill_id))
    return SkillOut.model_validate(skill)


@skills_router.patch("/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: str, payload: SkillUpdate, user: CurrentUser, service: SkillServiceDep
) -> SkillOut:
    updates = payload.model_dump(exclude_unset=True)
    skill = await service.update_skill(SkillId.parse(skill_id), **updates)
    return SkillOut.model_validate(skill)


@skills_router.post("/{skill_id}/status", response_model=SkillOut)
async def change_skill_status(
    skill_id: str, payload: SkillStatusChange, user: CurrentUser, service: SkillServiceDep
) -> SkillOut:
    skill = await service.set_skill_status(SkillId.parse(skill_id), payload.status)
    return SkillOut.model_validate(skill)


@tools_router.post("", status_code=status.HTTP_201_CREATED, response_model=ToolOut)
async def register_tool(payload: ToolCreate, user: CurrentUser, service: ToolServiceDep) -> ToolOut:
    tool = await service.register_tool(
        name=payload.name,
        version=payload.version,
        description=payload.description,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        capabilities=payload.capabilities,
        configuration=payload.configuration,
    )
    return ToolOut.model_validate(tool)


@tools_router.get("", response_model=list[ToolOut])
async def list_tools(
    user: CurrentUser, service: ToolServiceDep, name: str | None = Query(default=None)
) -> list[ToolOut]:
    if name:
        tools = await service.list_tool_versions(name)
    else:
        tools = await service.list_tools()
    return [ToolOut.model_validate(t) for t in tools]


@tools_router.get("/{tool_id}", response_model=ToolOut)
async def get_tool(tool_id: str, user: CurrentUser, service: ToolServiceDep) -> ToolOut:
    tool = await service.get_tool(ToolId.parse(tool_id))
    return ToolOut.model_validate(tool)


@tools_router.patch("/{tool_id}", response_model=ToolOut)
async def update_tool(
    tool_id: str, payload: ToolUpdate, user: CurrentUser, service: ToolServiceDep
) -> ToolOut:
    updates = payload.model_dump(exclude_unset=True)
    tool = await service.update_tool(ToolId.parse(tool_id), **updates)
    return ToolOut.model_validate(tool)


@tools_router.post("/{tool_id}/status", response_model=ToolOut)
async def change_tool_status(
    tool_id: str, payload: ToolStatusChange, user: CurrentUser, service: ToolServiceDep
) -> ToolOut:
    tool = await service.set_tool_status(ToolId.parse(tool_id), payload.status)
    return ToolOut.model_validate(tool)


@models_router.post("", status_code=status.HTTP_201_CREATED, response_model=ModelOut)
async def register_model(
    payload: ModelCreate, user: CurrentUser, service: ModelServiceDep
) -> ModelOut:
    model = await service.register_model(
        name=payload.name,
        version=payload.version,
        provider=payload.provider,
        description=payload.description,
        capabilities=payload.capabilities,
        configuration=payload.configuration,
    )
    return ModelOut.model_validate(model)


@models_router.get("", response_model=list[ModelOut])
async def list_models(
    user: CurrentUser, service: ModelServiceDep, name: str | None = Query(default=None)
) -> list[ModelOut]:
    if name:
        models = await service.list_model_versions(name)
    else:
        models = await service.list_models()
    return [ModelOut.model_validate(m) for m in models]


@models_router.get("/{model_id}", response_model=ModelOut)
async def get_model(model_id: str, user: CurrentUser, service: ModelServiceDep) -> ModelOut:
    model = await service.get_model(ModelId.parse(model_id))
    return ModelOut.model_validate(model)


@models_router.patch("/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: str, payload: ModelUpdate, user: CurrentUser, service: ModelServiceDep
) -> ModelOut:
    updates = payload.model_dump(exclude_unset=True)
    model = await service.update_model(ModelId.parse(model_id), **updates)
    return ModelOut.model_validate(model)


@models_router.post("/{model_id}/status", response_model=ModelOut)
async def change_model_status(
    model_id: str, payload: ModelStatusChange, user: CurrentUser, service: ModelServiceDep
) -> ModelOut:
    model = await service.set_model_status(ModelId.parse(model_id), payload.status)
    return ModelOut.model_validate(model)
