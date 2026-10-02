#!/usr/bin/env python3
"""Dependency-free authorization primitives for the operator CRM.

Authentication belongs at the HTTP/admin boundary. The CRM accepts only an
explicit OperatorPrincipal, so a caller cannot impersonate an operator merely
by passing an arbitrary display name.
"""
from __future__ import annotations

from dataclasses import dataclass

ROLES = frozenset({"viewer", "operator", "supervisor", "admin"})
ROLE_LEVEL = {"viewer": 10, "operator": 20, "supervisor": 30, "admin": 40}


@dataclass(frozen=True)
class OperatorPrincipal:
    operator_id: str
    role: str

    def __post_init__(self) -> None:
        if not self.operator_id or len(self.operator_id) > 128:
            raise ValueError("operator_id must be non-empty and <= 128 characters")
        if self.role not in ROLES:
            raise ValueError("unknown operator role")

    def require(self, minimum_role: str) -> None:
        if minimum_role not in ROLES:
            raise ValueError("unknown required role")
        if ROLE_LEVEL[self.role] < ROLE_LEVEL[minimum_role]:
            raise PermissionError(
                f"operator role {self.role!r} cannot perform {minimum_role!r} action"
            )


__all__ = ["OperatorPrincipal", "ROLES", "ROLE_LEVEL"]
