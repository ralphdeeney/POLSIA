"""Agent, Skill, Tool and Model registry entities (frozen dataclasses)."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from polsia.core.registry.values import RegistryStatus, validate_semver
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import AgentId, CompanyId, ModelId, SkillId, ToolId


def _require_name(name: str) -> str:
    if not name.strip():
        raise ValidationError("name must not be empty")
    return name.strip()


def _copy_list(value: list[str] | None) -> list[str]:
    return list(value or [])


@dataclasses.dataclass(frozen=True)
class Agent:
    """Company-scoped agent configuration. ``type`` is free-form — the core
    never restricts it to a fixed set of agent types."""

    id: AgentId
    company_id: CompanyId
    name: str
    type: str = "generic"
    description: str | None = None
    status: RegistryStatus = RegistryStatus.ACTIVE
    model_reference: str | None = None  # free-form, e.g. a registered model name@version
    system_instructions: str | None = None
    capabilities: list[str] = dataclasses.field(default_factory=list)
    configuration: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        company_id: CompanyId,
        name: str,
        type: str = "generic",  # noqa: A002 - domain term from the approved spec
        description: str | None = None,
        model_reference: str | None = None,
        system_instructions: str | None = None,
        capabilities: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> Agent:
        ts = now or utcnow()
        return Agent(
            id=AgentId.generate(),
            company_id=company_id,
            name=_require_name(name),
            type=type.strip() or "generic",
            description=description,
            model_reference=model_reference,
            system_instructions=system_instructions,
            capabilities=_copy_list(capabilities),
            configuration=dict(configuration or {}),
            created_at=ts,
            updated_at=ts,
        )

    def _mutated(self, **changes: Any) -> Agent:
        return dataclasses.replace(self, updated_at=utcnow(), **changes)

    def update(
        self,
        *,
        name: str | None = None,
        type: str | None = None,  # noqa: A002
        description: str | None = None,
        model_reference: str | None = None,
        system_instructions: str | None = None,
        capabilities: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> Agent:
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = _require_name(name)
        if type is not None:
            changes["type"] = type.strip() or "generic"
        if description is not None:
            changes["description"] = description
        if model_reference is not None:
            changes["model_reference"] = model_reference
        if system_instructions is not None:
            changes["system_instructions"] = system_instructions
        if capabilities is not None:
            changes["capabilities"] = _copy_list(capabilities)
        if configuration is not None:
            changes["configuration"] = dict(configuration)
        return self._mutated(**changes)

    def disable(self) -> Agent:
        if self.status is RegistryStatus.DISABLED:
            raise ValidationError("agent is already disabled")
        return self._mutated(status=RegistryStatus.DISABLED)

    def enable(self) -> Agent:
        if self.status is RegistryStatus.ACTIVE:
            raise ValidationError("agent is already active")
        return self._mutated(status=RegistryStatus.ACTIVE)

    def is_selectable(self) -> bool:
        return self.status is RegistryStatus.ACTIVE


class _Versioned:
    """Shared helpers for versioned, platform-global registry entries."""

    name: str  # provided by the concrete dataclass (Skill/Tool/ModelEntry)
    version: str

    @property
    def key(self) -> str:
        """Unique identity: ``name@version``."""
        return f"{self.name}@{self.version}"


@dataclasses.dataclass(frozen=True)
class Skill(_Versioned):
    """Platform-global, versioned skill. Executions record the exact skill
    version used (reproducibility)."""

    id: SkillId
    name: str
    version: str
    description: str | None = None
    status: RegistryStatus = RegistryStatus.ACTIVE
    input_schema: dict[str, Any] = dataclasses.field(default_factory=dict)
    output_schema: dict[str, Any] = dataclasses.field(default_factory=dict)
    required_capabilities: list[str] = dataclasses.field(default_factory=list)
    required_tools: list[str] = dataclasses.field(default_factory=list)
    required_permissions: list[str] = dataclasses.field(default_factory=list)
    configuration: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        name: str,
        version: str,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        required_capabilities: list[str] | None = None,
        required_tools: list[str] | None = None,
        required_permissions: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> Skill:
        validate_semver(version)
        ts = now or utcnow()
        return Skill(
            id=SkillId.generate(),
            name=_require_name(name),
            version=version,
            description=description,
            input_schema=dict(input_schema or {}),
            output_schema=dict(output_schema or {}),
            required_capabilities=_copy_list(required_capabilities),
            required_tools=_copy_list(required_tools),
            required_permissions=_copy_list(required_permissions),
            configuration=dict(configuration or {}),
            created_at=ts,
            updated_at=ts,
        )

    def _mutated(self, **changes: Any) -> Skill:
        return dataclasses.replace(self, updated_at=utcnow(), **changes)

    def update(
        self,
        *,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        required_capabilities: list[str] | None = None,
        required_tools: list[str] | None = None,
        required_permissions: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> Skill:
        changes: dict[str, Any] = {}
        if description is not None:
            changes["description"] = description
        if input_schema is not None:
            changes["input_schema"] = dict(input_schema)
        if output_schema is not None:
            changes["output_schema"] = dict(output_schema)
        if required_capabilities is not None:
            changes["required_capabilities"] = _copy_list(required_capabilities)
        if required_tools is not None:
            changes["required_tools"] = _copy_list(required_tools)
        if required_permissions is not None:
            changes["required_permissions"] = _copy_list(required_permissions)
        if configuration is not None:
            changes["configuration"] = dict(configuration)
        return self._mutated(**changes)

    def disable(self) -> Skill:
        if self.status is RegistryStatus.DISABLED:
            raise ValidationError("skill is already disabled")
        return self._mutated(status=RegistryStatus.DISABLED)

    def enable(self) -> Skill:
        if self.status is RegistryStatus.ACTIVE:
            raise ValidationError("skill is already active")
        return self._mutated(status=RegistryStatus.ACTIVE)

    def is_selectable(self) -> bool:
        return self.status is RegistryStatus.ACTIVE


@dataclasses.dataclass(frozen=True)
class Tool(_Versioned):
    """Platform-global, versioned tool. The core knows only the abstract
    contract; implementations live behind adapters (ports, Phase 2+)."""

    id: ToolId
    name: str
    version: str
    description: str | None = None
    status: RegistryStatus = RegistryStatus.ACTIVE
    input_schema: dict[str, Any] = dataclasses.field(default_factory=dict)
    output_schema: dict[str, Any] = dataclasses.field(default_factory=dict)
    capabilities: list[str] = dataclasses.field(default_factory=list)
    configuration: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        name: str,
        version: str,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> Tool:
        validate_semver(version)
        ts = now or utcnow()
        return Tool(
            id=ToolId.generate(),
            name=_require_name(name),
            version=version,
            description=description,
            input_schema=dict(input_schema or {}),
            output_schema=dict(output_schema or {}),
            capabilities=_copy_list(capabilities),
            configuration=dict(configuration or {}),
            created_at=ts,
            updated_at=ts,
        )

    def _mutated(self, **changes: Any) -> Tool:
        return dataclasses.replace(self, updated_at=utcnow(), **changes)

    def update(
        self,
        *,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> Tool:
        changes: dict[str, Any] = {}
        if description is not None:
            changes["description"] = description
        if input_schema is not None:
            changes["input_schema"] = dict(input_schema)
        if output_schema is not None:
            changes["output_schema"] = dict(output_schema)
        if capabilities is not None:
            changes["capabilities"] = _copy_list(capabilities)
        if configuration is not None:
            changes["configuration"] = dict(configuration)
        return self._mutated(**changes)

    def disable(self) -> Tool:
        if self.status is RegistryStatus.DISABLED:
            raise ValidationError("tool is already disabled")
        return self._mutated(status=RegistryStatus.DISABLED)

    def enable(self) -> Tool:
        if self.status is RegistryStatus.ACTIVE:
            raise ValidationError("tool is already active")
        return self._mutated(status=RegistryStatus.ACTIVE)

    def is_selectable(self) -> bool:
        return self.status is RegistryStatus.ACTIVE


@dataclasses.dataclass(frozen=True)
class ModelEntry(_Versioned):
    """Platform-global model/provider capability description. Provider is a
    free-form label; the core is not coupled to any vendor."""

    id: ModelId
    name: str
    version: str
    provider: str = "generic"
    description: str | None = None
    status: RegistryStatus = RegistryStatus.ACTIVE
    capabilities: list[str] = dataclasses.field(default_factory=list)
    configuration: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        name: str,
        version: str,
        provider: str = "generic",
        description: str | None = None,
        capabilities: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> ModelEntry:
        validate_semver(version)
        ts = now or utcnow()
        return ModelEntry(
            id=ModelId.generate(),
            name=_require_name(name),
            version=version,
            provider=provider.strip() or "generic",
            description=description,
            capabilities=_copy_list(capabilities),
            configuration=dict(configuration or {}),
            created_at=ts,
            updated_at=ts,
        )

    def _mutated(self, **changes: Any) -> ModelEntry:
        return dataclasses.replace(self, updated_at=utcnow(), **changes)

    def update(
        self,
        *,
        description: str | None = None,
        provider: str | None = None,
        capabilities: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> ModelEntry:
        changes: dict[str, Any] = {}
        if description is not None:
            changes["description"] = description
        if provider is not None:
            changes["provider"] = provider.strip() or "generic"
        if capabilities is not None:
            changes["capabilities"] = _copy_list(capabilities)
        if configuration is not None:
            changes["configuration"] = dict(configuration)
        return self._mutated(**changes)

    def disable(self) -> ModelEntry:
        if self.status is RegistryStatus.DISABLED:
            raise ValidationError("model is already disabled")
        return self._mutated(status=RegistryStatus.DISABLED)

    def enable(self) -> ModelEntry:
        if self.status is RegistryStatus.ACTIVE:
            raise ValidationError("model is already active")
        return self._mutated(status=RegistryStatus.ACTIVE)

    def is_selectable(self) -> bool:
        return self.status is RegistryStatus.ACTIVE
