import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.security import utcnow
from app.db.session import get_db
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus, RefreshSession
from app.schemas.auth import EmployeeDirectoryItem, EmployeeDirectoryPage, EmployeeResponse, EmployeeUpdate

router = APIRouter(tags=["employees"])
ADMIN_ONLY = require_roles(EmployeeRole.ADMIN)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"status": "error", "message": "Employee not found"})


def _audit(
    db: Session,
    *,
    actor: Employee,
    target: Employee,
    action: str,
    details: dict[str, object],
    happened_at: datetime,
) -> None:
    db.add(
        AuditLog(
            actor_employee_id=actor.id,
            target_employee_id=target.id,
            action=action,
            details=details,
            created_at=happened_at,
        )
    )


@router.get("/employees", response_model=EmployeeDirectoryPage)
def list_employees(
    search: str | None = Query(default=None, max_length=100),
    role: EmployeeRole | None = None,
    account_status: EmployeeStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _admin: Employee = Depends(ADMIN_ONLY),
    db: Session = Depends(get_db),
) -> EmployeeDirectoryPage:
    criteria = []
    if search and search.strip():
        needle = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{needle}%"
        criteria.append(
            Employee.full_name.ilike(pattern, escape="\\")
            | Employee.email.ilike(pattern, escape="\\")
        )
    if role is not None:
        criteria.append(Employee.role == role)
    if account_status is not None:
        criteria.append(Employee.status == account_status)

    total = db.scalar(select(func.count(Employee.id)).where(*criteria)) or 0
    employees = db.scalars(
        select(Employee)
        .where(*criteria)
        .order_by(Employee.created_at.desc(), Employee.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return EmployeeDirectoryPage(
        items=[EmployeeDirectoryItem.model_validate(employee) for employee in employees],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/employees/{employee_id}", response_model=EmployeeDirectoryItem)
def get_employee(
    employee_id: uuid.UUID,
    _admin: Employee = Depends(ADMIN_ONLY),
    db: Session = Depends(get_db),
) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise _not_found()
    return employee


@router.patch("/employees/{employee_id}", response_model=EmployeeDirectoryItem)
def update_employee(
    employee_id: uuid.UUID,
    data: EmployeeUpdate,
    admin: Employee = Depends(ADMIN_ONLY),
    db: Session = Depends(get_db),
) -> Employee:
    target = db.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())
    if target is None:
        raise _not_found()

    changes = data.model_dump(exclude_unset=True)
    requested_role = changes.get("role")
    requested_status = changes.get("status")

    if target.id == admin.id and requested_role is not None and requested_role != target.role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "forbidden", "message": "Admins cannot change their own role"},
        )

    final_role = requested_role if requested_role is not None else target.role
    final_status = EmployeeStatus(requested_status.value) if requested_status is not None else target.status
    is_leaving_active_admin = (
        target.role == EmployeeRole.ADMIN
        and target.status == EmployeeStatus.ACTIVE
        and (final_role != EmployeeRole.ADMIN or final_status != EmployeeStatus.ACTIVE)
    )
    if is_leaving_active_admin:
        active_admins = db.scalars(
            select(Employee)
            .where(Employee.role == EmployeeRole.ADMIN, Employee.status == EmployeeStatus.ACTIVE)
            .with_for_update()
        ).all()
        if len(active_admins) <= 1:
            raise HTTPException(
                status_code=409,
                detail={"status": "conflict", "message": "The last active Admin cannot be demoted or deactivated"},
            )

    now = utcnow()
    changed = False
    requested_name = changes.get("full_name")
    if requested_name is not None and requested_name != target.full_name:
        previous_name = target.full_name
        target.full_name = requested_name
        _audit(
            db,
            actor=admin,
            target=target,
            action="employee.updated",
            details={"changes": {"full_name": {"from": previous_name, "to": requested_name}}},
            happened_at=now,
        )
        changed = True

    if requested_role is not None and requested_role != target.role:
        previous_role = target.role.value
        target.role = requested_role
        _audit(
            db,
            actor=admin,
            target=target,
            action="employee.role_changed",
            details={"from": previous_role, "to": requested_role.value},
            happened_at=now,
        )
        changed = True

    if requested_status is not None and final_status != target.status:
        previous_status = target.status
        target.status = final_status
        action = "employee.activated" if final_status == EmployeeStatus.ACTIVE else "employee.deactivated"
        _audit(
            db,
            actor=admin,
            target=target,
            action=action,
            details={"from": previous_status.value, "to": final_status.value},
            happened_at=now,
        )
        if final_status == EmployeeStatus.DISABLED:
            db.execute(
                update(RefreshSession)
                .where(RefreshSession.employee_id == target.id, RefreshSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
        changed = True

    if changed:
        db.commit()
        db.refresh(target)
    return target
