"""Authentication endpoints: register, login, current user."""

from __future__ import annotations

from fastapi import APIRouter, status

from polsia.api.deps import AuthServiceDep, CurrentUser, SettingsDep
from polsia.api.schemas import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserOut,
)

router = APIRouter(tags=["auth"])


@router.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
)
async def register(
    payload: RegisterRequest, auth: AuthServiceDep, settings: SettingsDep
) -> RegisterResponse:
    """Create a user account and return the user plus a token pair."""
    user = await auth.register(payload.email, payload.password, payload.display_name)
    access, refresh = auth.issue_token_pair(user)
    return RegisterResponse(
        user=UserOut.model_validate(user),
        tokens=TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=settings.access_token_ttl_minutes * 60,
        ),
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, auth: AuthServiceDep, settings: SettingsDep
) -> TokenResponse:
    """Authenticate with email/password and return a fresh token pair."""
    user = await auth.authenticate(payload.email, payload.password)
    access, refresh = auth.issue_token_pair(user)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.get("/auth/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    """Return the currently authenticated user."""
    return UserOut.model_validate(user)
