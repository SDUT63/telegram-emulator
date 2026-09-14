import pytest

from operator_auth import OperatorPrincipal


def test_operator_principal_rejects_unknown_role():
    with pytest.raises(ValueError):
        OperatorPrincipal("op-1", "root")


def test_viewer_can_read_but_not_mutate():
    principal = OperatorPrincipal("op-1", "viewer")
    principal.require("viewer")
    with pytest.raises(PermissionError):
        principal.require("operator")


def test_operator_can_mutate_but_not_supervise():
    principal = OperatorPrincipal("op-1", "operator")
    principal.require("operator")
    with pytest.raises(PermissionError):
        principal.require("supervisor")


def test_admin_satisfies_all_roles():
    principal = OperatorPrincipal("admin", "admin")
    for role in ("viewer", "operator", "supervisor", "admin"):
        principal.require(role)
