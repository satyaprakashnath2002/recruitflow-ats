import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_employee, require_roles
from app.db.session import get_db
from app.models.auth import Employee, EmployeeRole, EmployeeStatus
from app.models.jobs import Job, JobAssignment, JobStatus
from app.schemas.jobs import (
    JobAssignmentList,
    JobAssignmentResponse,
    JobCreate,
    JobPage,
    JobPublicResponse,
    JobResponse,
    JobUpdate,
    PublicJobPage,
    validate_ranges,
)
from app.services.job_management import JOB_STATUS_ACTIONS, JOB_TRANSITIONS, record_job_audit

router = APIRouter(tags=["jobs"])
MANAGERS = require_roles(EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER)

SORT_FIELDS = {
    "title": Job.title,
    "created_at": Job.created_at,
    "updated_at": Job.updated_at,
    "deadline": Job.application_deadline,
    "department": Job.department,
}


def _error(code: int, status_text: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"status": status_text, "message": message})


def _job_or_404(db: Session, job_id: uuid.UUID, *, lock: bool = False) -> Job:
    statement = select(Job).where(Job.id == job_id)
    if lock:
        statement = statement.with_for_update()
    job = db.scalar(statement)
    if job is None:
        raise _error(404, "not_found", "Job not found")
    return job


def _readable_job(employee: Employee, job: Job, db: Session) -> None:
    if employee.role in (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER):
        return
    if employee.role == EmployeeRole.RECRUITER:
        assigned = db.scalar(
            select(JobAssignment.job_id).where(
                JobAssignment.job_id == job.id,
                JobAssignment.employee_id == employee.id,
            )
        )
        if assigned is not None:
            return
        raise _error(404, "not_found", "Job not found")
    raise _error(403, "forbidden", "Insufficient permissions")


def _filters(
    *,
    search: str | None,
    department: str | None,
    employment_type: str | None,
    location: str | None,
    work_mode: str | None,
    experience_min: Decimal | None,
    experience_max: Decimal | None,
) -> list:
    criteria = []
    if search and search.strip():
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        criteria.append(Job.title.ilike(f"%{escaped}%", escape="\\"))
    for column, value in (
        (Job.department, department),
        (Job.employment_type, employment_type),
        (Job.location, location),
        (Job.work_mode, work_mode),
    ):
        if value and value.strip():
            criteria.append(func.lower(column) == value.strip().lower())
    if experience_min is not None:
        criteria.append(or_(Job.max_experience.is_(None), Job.max_experience >= experience_min))
    if experience_max is not None:
        criteria.append(or_(Job.min_experience.is_(None), Job.min_experience <= experience_max))
    return criteria


def _sort_statement(sort_by: str, order: str):
    column = SORT_FIELDS[sort_by]
    return column.asc().nullslast() if order == "asc" else column.desc().nullslast()


def _job_list_query(
    db: Session,
    *,
    criteria: list,
    page: int,
    page_size: int,
    sort_by: str,
    order: str,
) -> tuple[list[Job], int]:
    total = db.scalar(select(func.count(Job.id)).where(*criteria)) or 0
    jobs = db.scalars(
        select(Job)
        .where(*criteria)
        .order_by(_sort_statement(sort_by, order), Job.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return jobs, total


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    data: JobCreate,
    actor: Employee = Depends(MANAGERS),
    db: Session = Depends(get_db),
) -> Job:
    job = Job(**data.model_dump(), status=JobStatus.DRAFT.value, created_by_id=actor.id, updated_by_id=actor.id)
    db.add(job)
    db.flush()
    record_job_audit(db, actor=actor, job=job, action="job.created", details={"status": "draft"})
    db.commit()
    db.refresh(job)
    return job


@router.get("/jobs", response_model=JobPage)
def list_jobs(
    search: str | None = Query(default=None, max_length=100),
    status_filter: JobStatus | None = Query(default=None, alias="status"),
    department: str | None = Query(default=None, max_length=120),
    employment_type: str | None = Query(default=None, max_length=50),
    location: str | None = Query(default=None, max_length=200),
    work_mode: str | None = Query(default=None, max_length=50),
    experience_min: Decimal | None = Query(default=None, ge=0, max_digits=5, decimal_places=2),
    experience_max: Decimal | None = Query(default=None, ge=0, max_digits=5, decimal_places=2),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="created_at", pattern="^(title|created_at|updated_at|deadline|department)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> JobPage:
    if actor.role not in (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER, EmployeeRole.RECRUITER):
        raise _error(403, "forbidden", "Insufficient permissions")
    criteria = _filters(
        search=search,
        department=department,
        employment_type=employment_type,
        location=location,
        work_mode=work_mode,
        experience_min=experience_min,
        experience_max=experience_max,
    )
    if experience_min is not None and experience_max is not None and experience_min > experience_max:
        raise _error(422, "error", "experience_min cannot exceed experience_max")
    if status_filter is not None:
        criteria.append(Job.status == status_filter.value)
    if actor.role == EmployeeRole.RECRUITER:
        criteria.append(
            Job.id.in_(select(JobAssignment.job_id).where(JobAssignment.employee_id == actor.id))
        )
    jobs, total = _job_list_query(
        db, criteria=criteria, page=page, page_size=page_size, sort_by=sort_by, order=order
    )
    return JobPage(items=jobs, page=page, page_size=page_size, total=total)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: uuid.UUID,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> Job:
    job = _job_or_404(db, job_id)
    _readable_job(actor, job, db)
    return job


@router.patch("/jobs/{job_id}", response_model=JobResponse)
def update_job(
    job_id: uuid.UUID,
    data: JobUpdate,
    actor: Employee = Depends(MANAGERS),
    db: Session = Depends(get_db),
) -> Job:
    job = _job_or_404(db, job_id, lock=True)
    changes = data.model_dump(exclude_unset=True)
    merged = {
        "min_experience": changes.get("min_experience", job.min_experience),
        "max_experience": changes.get("max_experience", job.max_experience),
        "salary_min": changes.get("salary_min", job.salary_min),
        "salary_max": changes.get("salary_max", job.salary_max),
    }
    try:
        validate_ranges(**merged)
    except ValueError as error:
        raise _error(422, "error", str(error)) from error

    changed_fields = [field for field, value in changes.items() if getattr(job, field) != value]
    if changed_fields:
        for field, value in changes.items():
            setattr(job, field, value)
        job.updated_by_id = actor.id
        record_job_audit(
            db,
            actor=actor,
            job=job,
            action="job.updated",
            details={"changed_fields": sorted(changed_fields)},
        )
        db.commit()
        db.refresh(job)
    return job


@router.post("/jobs/{job_id}/publish", response_model=JobResponse)
@router.post("/jobs/{job_id}/pause", response_model=JobResponse)
@router.post("/jobs/{job_id}/close", response_model=JobResponse)
@router.post("/jobs/{job_id}/archive", response_model=JobResponse)
def transition_job(
    job_id: uuid.UUID,
    request: Request,
    actor: Employee = Depends(MANAGERS),
    db: Session = Depends(get_db),
) -> Job:
    action = request.url.path.rsplit("/", 1)[-1]
    target_status = {"publish": "published", "pause": "paused", "close": "closed", "archive": "archived"}[action]
    job = _job_or_404(db, job_id, lock=True)
    current = job.status.value if isinstance(job.status, JobStatus) else job.status
    if target_status not in JOB_TRANSITIONS.get(current, set()):
        raise _error(400, "invalid_transition", f"Cannot transition job from {current} to {target_status}")
    job.status = target_status
    job.updated_by_id = actor.id
    record_job_audit(
        db,
        actor=actor,
        job=job,
        action=JOB_STATUS_ACTIONS[target_status],
        details={"from": current, "to": target_status},
    )
    db.commit()
    db.refresh(job)
    return job


@router.get("/jobs/{job_id}/assignments", response_model=JobAssignmentList)
def list_job_assignments(
    job_id: uuid.UUID,
    _actor: Employee = Depends(MANAGERS),
    db: Session = Depends(get_db),
) -> JobAssignmentList:
    job = _job_or_404(db, job_id)
    rows = db.execute(
        select(JobAssignment, Employee)
        .join(Employee, Employee.id == JobAssignment.employee_id)
        .where(JobAssignment.job_id == job.id)
        .order_by(Employee.full_name, Employee.email)
    ).all()
    return JobAssignmentList(
        job_id=job.id,
        recruiters=[
            JobAssignmentResponse(
                employee_id=employee.id,
                full_name=employee.full_name,
                email=employee.email,
                assigned_by_id=assignment.assigned_by_id,
                assigned_at=assignment.assigned_at,
            )
            for assignment, employee in rows
        ],
    )


@router.put(
    "/jobs/{job_id}/assignments/{employee_id}",
    response_model=JobAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def assign_recruiter(
    job_id: uuid.UUID,
    employee_id: uuid.UUID,
    actor: Employee = Depends(MANAGERS),
    db: Session = Depends(get_db),
) -> JobAssignmentResponse:
    job = _job_or_404(db, job_id, lock=True)
    employee = db.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())
    if employee is None:
        raise _error(404, "not_found", "Employee not found")
    if employee.role != EmployeeRole.RECRUITER:
        raise _error(422, "invalid_employee", "Only employees with the Recruiter role can be assigned")
    if employee.status != EmployeeStatus.ACTIVE:
        raise _error(422, "invalid_employee", "Only active Recruiters can be assigned")
    if db.get(JobAssignment, (job.id, employee.id)) is not None:
        raise _error(409, "conflict", "Recruiter is already assigned to this job")

    assignment = JobAssignment(job_id=job.id, employee_id=employee.id, assigned_by_id=actor.id)
    db.add(assignment)
    record_job_audit(
        db,
        actor=actor,
        job=job,
        action="job.recruiter_assigned",
        details={"employee_id": str(employee.id)},
    )
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise _error(409, "conflict", "Recruiter is already assigned to this job") from error
    db.refresh(assignment)
    return JobAssignmentResponse(
        employee_id=employee.id,
        full_name=employee.full_name,
        email=employee.email,
        assigned_by_id=assignment.assigned_by_id,
        assigned_at=assignment.assigned_at,
    )


@router.delete("/jobs/{job_id}/assignments/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_recruiter(
    job_id: uuid.UUID,
    employee_id: uuid.UUID,
    actor: Employee = Depends(MANAGERS),
    db: Session = Depends(get_db),
) -> Response:
    job = _job_or_404(db, job_id, lock=True)
    assignment = db.get(JobAssignment, (job.id, employee_id))
    if assignment is None:
        raise _error(404, "not_found", "Job assignment not found")
    db.add(assignment)
    record_job_audit(
        db,
        actor=actor,
        job=job,
        action="job.recruiter_removed",
        details={"employee_id": str(employee_id)},
    )
    db.delete(assignment)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/public/jobs", response_model=PublicJobPage)
def list_public_jobs(
    search: str | None = Query(default=None, max_length=100),
    department: str | None = Query(default=None, max_length=120),
    employment_type: str | None = Query(default=None, max_length=50),
    location: str | None = Query(default=None, max_length=200),
    work_mode: str | None = Query(default=None, max_length=50),
    experience_min: Decimal | None = Query(default=None, ge=0, max_digits=5, decimal_places=2),
    experience_max: Decimal | None = Query(default=None, ge=0, max_digits=5, decimal_places=2),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="created_at", pattern="^(title|created_at|deadline|department)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> PublicJobPage:
    if experience_min is not None and experience_max is not None and experience_min > experience_max:
        raise _error(422, "error", "experience_min cannot exceed experience_max")
    criteria = [Job.status == JobStatus.PUBLISHED.value]
    criteria.extend(
        _filters(
            search=search,
            department=department,
            employment_type=employment_type,
            location=location,
            work_mode=work_mode,
            experience_min=experience_min,
            experience_max=experience_max,
        )
    )
    jobs, total = _job_list_query(
        db, criteria=criteria, page=page, page_size=page_size, sort_by=sort_by, order=order
    )
    return PublicJobPage(items=jobs, page=page, page_size=page_size, total=total)


@router.get("/public/jobs/{job_id}", response_model=JobPublicResponse)
def get_public_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> Job:
    job = db.scalar(
        select(Job).where(Job.id == job_id, Job.status == JobStatus.PUBLISHED.value)
    )
    if job is None:
        raise _error(404, "not_found", "Job not found")
    return job
