"""Authentication & authorisation.

JWT validation (signature, expiry, issuer, audience) is done by the API Gateway
JWT authorizer *before* Lambda runs, so the app never sees an invalid token.
Here we only read the already-verified claims that API Gateway forwards in the
Lambda event and apply role checks (Cognito group ``admin``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status

from app.config import get_settings
from app.observability import user_var


@dataclass
class Principal:
    sub: str
    email: str | None = None
    groups: list[str] = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return get_settings().admin_group in self.groups


def _parse_groups(raw: object) -> list[str]:
    # HTTP API forwards array claims as a string such as "[admin engineers]".
    if isinstance(raw, list):
        return [str(g) for g in raw]
    if isinstance(raw, str):
        return [g for g in raw.strip("[]").replace(",", " ").split() if g]
    return []


async def get_principal(request: Request) -> Principal:
    settings = get_settings()
    if settings.auth_mode == "none":
        principal = Principal(sub="local-dev", email="dev@localhost", groups=[settings.admin_group])
    else:
        event = request.scope.get("aws.event") or {}
        claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims")
        if not claims or "sub" not in claims:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid token")
        principal = Principal(
            sub=str(claims["sub"]),
            email=claims.get("email"),
            groups=_parse_groups(claims.get("cognito:groups")),
        )
    user_var.set(principal.sub)
    return principal


async def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return principal
