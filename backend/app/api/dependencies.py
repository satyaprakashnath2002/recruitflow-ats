import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_jwt
from app.db.session import get_db
from app.models.auth import Employee, EmployeeRole, EmployeeStatus
from app.services.auth_tokens import get_session, session_is_active

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_employee(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Employee:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"status": "unauthorized", "message": "Authentication required"},
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = decode_jwt(credentials.credentials, "access")
        employee_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except ValueError:
        raise unauthorized from None

    session = get_session(db, session_id)
    employee = db.scalar(select(Employee).where(Employee.id == employee_id))
    if (
        session is None
        or not session_is_active(session)
        or session.employee_id != employee_id
        or employee is None
        or employee.status != EmployeeStatus.ACTIVE
    ):
        raise unauthorized
    return employee


def require_roles(*allowed_roles: EmployeeRole) -> Callable:
    def dependency(employee: Employee = Depends(get_current_employee)) -> Employee:
        if employee.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"status": "forbidden", "message": "Insufficient permissions"},
            )
        return employee

    return dependency
