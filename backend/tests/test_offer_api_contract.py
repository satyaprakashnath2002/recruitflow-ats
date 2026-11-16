from fastapi.routing import APIRoute
from fastapi import HTTPException
import pytest

from app.api.v1.offers import router as offers_router
from app.main import app
from app.models.auth import Employee, EmployeeRole, EmployeeStatus


EMPLOYEE_PATHS = {
    "/applications/{application_id}/offers": (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER),
    "/offers": (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER),
    "/offers/{offer_id}": (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER),
    "/offers/{offer_id}/revisions": (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER),
    "/offers/{offer_id}/revisions/{revision_number}/letter": (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER),
    "/offers/{offer_id}/submit": (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER),
    "/offers/{offer_id}/approve": (EmployeeRole.HR_MANAGER,),
    "/offers/{offer_id}/send": (EmployeeRole.HR_MANAGER,),
    "/offers/{offer_id}/withdraw": (EmployeeRole.HR_MANAGER,),
}


def routes_for(path: str) -> list[APIRoute]:
    return [route for route in offers_router.routes if isinstance(route, APIRoute) and route.path == path]


def test_offer_api_routes_are_registered_and_have_employee_role_dependencies():
    api_paths = app.openapi()["paths"]
    for path, roles in EMPLOYEE_PATHS.items():
        assert f"/api/v1{path}" in api_paths
        for route in routes_for(path):
            actor_dependency = next(
                dependency for dependency in route.dependant.dependencies
                if dependency.name in {"actor", "_actor"}
            )
            assert actor_dependency.call.__name__ == "dependency"
            for role in roles:
                actor = Employee(full_name="Offer actor", email=f"{role.value}@example.test", role=role, status=EmployeeStatus.ACTIVE)
                assert actor_dependency.call(actor) is actor
            for role in set(EmployeeRole) - set(roles):
                actor = Employee(full_name="Denied actor", email=f"denied-{role.value}@example.test", role=role, status=EmployeeStatus.ACTIVE)
                with pytest.raises(HTTPException) as denied:
                    actor_dependency.call(actor)
                assert denied.value.status_code == 403


def test_public_offer_routes_require_capability_without_employee_authentication():
    api_paths = app.openapi()["paths"]
    for path, method in (
        ("/public/offers/current", "get"),
        ("/public/offers/letter", "get"),
        ("/public/offers/response", "post"),
    ):
        route = routes_for(path)[0]
        assert f"/api/v1{path}" in api_paths
        assert all(dependency.name not in {"actor", "_actor"} for dependency in route.dependant.dependencies)
        parameters = api_paths[f"/api/v1{path}"][method]["parameters"]
        capability = next(parameter for parameter in parameters if parameter["name"] == "X-Offer-Capability")
        assert capability["in"] == "header" and capability["required"] is True


def test_cross_origin_offer_capability_header_is_allowed():
    cors = next(middleware for middleware in app.user_middleware if middleware.cls.__name__ == "CORSMiddleware")
    assert "X-Offer-Capability" in cors.kwargs["allow_headers"]
