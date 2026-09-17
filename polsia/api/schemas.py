"""API request/response schemas (Pydantic v2).

Only the schemas the API layer needs to speak to clients; domain entities
stay plain dataclasses. Conversion happens in the routers via
``model_validate`` — domain entities are never returned directly.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from polsia.core.tenancy.values import CompanyStatus, MembershipRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=72,
        description="Plaintext password; min 8 chars, max 72 (bcrypt input limit)",
    )
    display_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str


class RegisterResponse(BaseModel):
    user: UserOut
    tokens: TokenResponse


# -- Organizations ---------------------------------------------------------


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=2, max_length=63)


class OrganizationUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    owner_user_id: str
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime


class MemberAdd(BaseModel):
    user_id: str
    role: MembershipRole


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    user_id: str
    role: str
    created_at: dt.datetime


# -- Companies ---------------------------------------------------------------


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=2, max_length=63)
    identity: dict[str, Any] | None = None
    mission: str | None = Field(default=None, max_length=2000)


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    identity: dict[str, Any] | None = None
    mission: str | None = Field(default=None, max_length=2000)
    strategy: dict[str, Any] | None = None
    configuration: dict[str, Any] | None = None


class CompanyStatusChange(BaseModel):
    status: CompanyStatus


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    name: str
    slug: str
    identity: dict[str, Any]
    mission: str | None
    strategy: dict[str, Any]
    configuration: dict[str, Any]
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime
