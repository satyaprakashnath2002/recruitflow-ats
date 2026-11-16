from datetime import date, timedelta
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.jobs import Job, JobAssignment

PASSWORD = "Job management test password 77!"


def create_employee(factory: sessionmaker, email: str, role: EmployeeRole, *, status=EmployeeStatus.ACTIVE):
    with factory() as db:
        employee = Employee(
            full_name=email.split("@")[0].replace("-", " ").title(),
            email=email,
            role=role,
            status=status,
            password_hash=hash_password(PASSWORD),
        )
        db.add(employee)
        db.commit()
        db.refresh(employee)
        return employee


def auth_headers(client, email: str) -> dict[str, str]:
    result = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert result.status_code == 200
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


def payload(**updates):
    data = {
        "title": "Senior Platform Engineer",
        "department": "Engineering",
        "employment_type": "Full-time",
        "location": "Bengaluru",
        "work_mode": "Hybrid",
        "min_experience": 3,
        "max_experience": 8,
        "salary_min": 100000,
        "salary_max": 200000,
        "description": "Build and maintain platform services.",
        "required_skills": ["Python", "PostgreSQL"],
        "qualifications": ["Relevant experience"],
        "vacancies": 2,
        "application_deadline": (date.today() + timedelta(days=30)).isoformat(),
    }
    data.update(updates)
    return data


def create_job(client, headers, **updates):
    response = client.post("/api/v1/jobs", json=payload(**updates), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, headers, job_id):
    result = client.post(f"/api/v1/jobs/{job_id}/publish", headers=headers)
    assert result.status_code == 200, result.text
    return result.json()


def test_admin_and_hr_manager_create_job_with_initial_draft_status(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "jobs-admin@example.com", EmployeeRole.ADMIN)
    hr = create_employee(factory, "jobs-hr@example.com", EmployeeRole.HR_MANAGER)
    admin_job = create_job(client, auth_headers(client, admin.email))
    hr_job = create_job(client, auth_headers(client, hr.email), title="HR Created Position")
    assert admin_job["status"] == hr_job["status"] == "draft"
    assert admin_job["created_by_id"] == admin_job["updated_by_id"]
    with factory() as db:
        events = db.scalars(select(AuditLog).where(AuditLog.action == "job.created")).all()
        assert {event.target_job_id for event in events} == {uuid.UUID(admin_job["id"]), uuid.UUID(hr_job["id"])}


@pytest.mark.parametrize("role", [EmployeeRole.RECRUITER, EmployeeRole.INTERVIEWER])
def test_recruiter_and_interviewer_cannot_create_jobs(api_client, role) -> None:
    client, factory, _ = api_client
    employee = create_employee(factory, f"{role.value}-create@example.com", role)
    response = client.post("/api/v1/jobs", json=payload(), headers=auth_headers(client, employee.email))
    assert response.status_code == 403


@pytest.mark.parametrize(
    "updates",
    [
        {"title": "  "},
        {"vacancies": 0},
        {"min_experience": 9, "max_experience": 2},
        {"min_experience": -1},
        {"salary_min": 5, "salary_max": 4},
        {"salary_max": -1},
        {"application_deadline": (date.today() - timedelta(days=1)).isoformat()},
        {"employment_type": None},
    ],
)
def test_invalid_job_data_is_rejected(api_client, updates) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "invalid-job-admin@example.com", EmployeeRole.ADMIN)
    response = client.post("/api/v1/jobs", json=payload(**updates), headers=auth_headers(client, admin.email))
    assert response.status_code == 422


def test_job_creation_requires_approved_fields(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "required-job-admin@example.com", EmployeeRole.ADMIN)
    headers = auth_headers(client, admin.email)
    data = payload()
    del data["title"]
    assert client.post("/api/v1/jobs", json=data, headers=headers).status_code == 422
    assert client.post("/api/v1/jobs", json=payload(), headers={}).status_code == 401


def test_internal_job_read_update_and_rbac(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "read-job-admin@example.com", EmployeeRole.ADMIN)
    hr = create_employee(factory, "read-job-hr@example.com", EmployeeRole.HR_MANAGER)
    recruiter = create_employee(factory, "read-job-recruiter@example.com", EmployeeRole.RECRUITER)
    interviewer = create_employee(factory, "read-job-interviewer@example.com", EmployeeRole.INTERVIEWER)
    admin_headers = auth_headers(client, admin.email)
    job = create_job(client, admin_headers)
    job_id = job["id"]

    assert client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers(client, hr.email)).status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers(client, interviewer.email)).status_code == 403
    assert client.get(f"/api/v1/jobs/not-a-uuid", headers=admin_headers).status_code == 422
    assert client.get(f"/api/v1/jobs/{uuid.uuid4()}", headers=admin_headers).status_code == 404
    updated = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"title": "Updated Platform Engineer", "salary_min": 105000},
        headers=auth_headers(client, hr.email),
    )
    assert updated.json()["updated_by_id"] == str(hr.id)
    with factory() as db:
        event = db.scalar(
            select(AuditLog).where(
                AuditLog.target_job_id == uuid.UUID(job_id), AuditLog.action == "job.updated"
            )
        )
        assert event is not None and event.action == "job.updated"
        assert event.details == {"changed_fields": ["salary_min", "title"]}
        assert "105000" not in str(event.details)
    assert client.patch(
        f"/api/v1/jobs/{job_id}", json={"title": "No permission"}, headers=auth_headers(client, recruiter.email)
    ).status_code == 403


def test_update_validates_merged_experience_and_salary_ranges(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "range-job-admin@example.com", EmployeeRole.ADMIN)
    headers = auth_headers(client, admin.email)
    job = create_job(client, headers)
    experience = client.patch(
        f"/api/v1/jobs/{job['id']}", json={"min_experience": 9}, headers=headers
    )
    salary = client.patch(f"/api/v1/jobs/{job['id']}", json={"salary_min": 250000}, headers=headers)
    assert experience.status_code == 422
    assert salary.status_code == 422
    with factory() as db:
        persisted = db.get(Job, uuid.UUID(job["id"]))
        assert persisted is not None
        assert persisted.min_experience == Decimal("3.00")
        assert persisted.salary_min == Decimal("100000.00")


def test_approved_job_lifecycle_and_audit_events(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "lifecycle-admin@example.com", EmployeeRole.ADMIN)
    recruiter = create_employee(factory, "lifecycle-recruiter@example.com", EmployeeRole.RECRUITER)
    admin_headers = auth_headers(client, admin.email)
    job = create_job(client, admin_headers)
    job_id = job["id"]

    assert client.get("/api/v1/jobs?status=draft", headers=admin_headers).json()["total"] == 1
    assert client.post(f"/api/v1/jobs/{job_id}/pause", headers=admin_headers).status_code == 400
    publish(client, admin_headers, job_id)
    assert client.get("/api/v1/jobs?status=published", headers=admin_headers).json()["total"] == 1
    assert client.post(f"/api/v1/jobs/{job_id}/archive", headers=admin_headers).status_code == 400
    assert client.post(
        f"/api/v1/jobs/{job_id}/close", headers=auth_headers(client, recruiter.email)
    ).status_code == 403
    assert client.post(f"/api/v1/jobs/{job_id}/pause", headers=admin_headers).status_code == 200
    assert client.post(f"/api/v1/jobs/{job_id}/publish", headers=admin_headers).status_code == 200
    assert client.post(f"/api/v1/jobs/{job_id}/close", headers=admin_headers).status_code == 200
    assert client.post(f"/api/v1/jobs/{job_id}/publish", headers=admin_headers).status_code == 400
    assert client.post(f"/api/v1/jobs/{job_id}/archive", headers=admin_headers).status_code == 200
    assert client.post(f"/api/v1/jobs/{job_id}/close", headers=admin_headers).status_code == 400
    with factory() as db:
        events = db.scalars(
            select(AuditLog).where(AuditLog.target_job_id == uuid.UUID(job_id)).order_by(AuditLog.created_at)
        ).all()
        transitions = [event for event in events if event.action.startswith("job.") and event.action != "job.created"]
        assert [(event.action, event.details["from"], event.details["to"]) for event in transitions] == [
            ("job.published", "draft", "published"),
            ("job.paused", "published", "paused"),
            ("job.published", "paused", "published"),
            ("job.closed", "published", "closed"),
            ("job.archived", "closed", "archived"),
        ]
        assert all(event.actor_employee_id == admin.id and event.created_at for event in transitions)


def test_multiple_active_recruiter_assignments_and_assignment_rbac(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "assignment-admin@example.com", EmployeeRole.ADMIN)
    hr = create_employee(factory, "assignment-hr@example.com", EmployeeRole.HR_MANAGER)
    recruiter1 = create_employee(factory, "assignment-recruiter-1@example.com", EmployeeRole.RECRUITER)
    recruiter2 = create_employee(factory, "assignment-recruiter-2@example.com", EmployeeRole.RECRUITER)
    non_recruiter = create_employee(factory, "assignment-other@example.com", EmployeeRole.INTERVIEWER)
    disabled = create_employee(
        factory, "assignment-disabled@example.com", EmployeeRole.RECRUITER, status=EmployeeStatus.DISABLED
    )
    admin_headers = auth_headers(client, admin.email)
    hr_headers = auth_headers(client, hr.email)
    job = create_job(client, admin_headers)
    job_id = job["id"]

    for recruiter, assigning_headers in ((recruiter1, admin_headers), (recruiter2, hr_headers)):
        assert client.put(
            f"/api/v1/jobs/{job_id}/assignments/{recruiter.id}", headers=assigning_headers
        ).status_code == 201
    assert client.put(
        f"/api/v1/jobs/{job_id}/assignments/{recruiter1.id}", headers=hr_headers
    ).status_code == 409
    assert client.put(
        f"/api/v1/jobs/{job_id}/assignments/{non_recruiter.id}", headers=hr_headers
    ).status_code == 422
    assert client.put(
        f"/api/v1/jobs/{job_id}/assignments/{disabled.id}", headers=hr_headers
    ).status_code == 422
    assert client.put(
        f"/api/v1/jobs/{job_id}/assignments/{recruiter1.id}", headers=auth_headers(client, recruiter1.email)
    ).status_code == 403
    assert client.delete(
        f"/api/v1/jobs/{job_id}/assignments/{recruiter1.id}", headers=auth_headers(client, recruiter1.email)
    ).status_code == 403
    assignments = client.get(f"/api/v1/jobs/{job_id}/assignments", headers=admin_headers)
    assert assignments.status_code == 200 and len(assignments.json()["recruiters"]) == 2

    recruiter1_headers = auth_headers(client, recruiter1.email)
    assert client.get(f"/api/v1/jobs/{job_id}", headers=recruiter1_headers).status_code == 200
    job_list = client.get("/api/v1/jobs", headers=recruiter1_headers)
    assert job_list.json()["total"] == 1
    unassigned_job = create_job(client, admin_headers, title="Unassigned Job")
    assert client.get(f"/api/v1/jobs/{unassigned_job['id']}", headers=recruiter1_headers).status_code == 404
    assert client.get("/api/v1/jobs", headers=recruiter1_headers).json()["total"] == 1
    assert client.patch(
        f"/api/v1/jobs/{job_id}", json={"title": "Recruiter edit"}, headers=recruiter1_headers
    ).status_code == 403
    assert client.delete(
        f"/api/v1/jobs/{job_id}/assignments/{recruiter2.id}", headers=admin_headers
    ).status_code == 204
    assert client.get(f"/api/v1/jobs/{job_id}/assignments", headers=admin_headers).json()["recruiters"]

    with factory() as db:
        events = db.scalars(select(AuditLog).where(AuditLog.target_job_id == uuid.UUID(job_id))).all()
        assert sum(event.action == "job.recruiter_assigned" for event in events) == 2
        assert sum(event.action == "job.recruiter_removed" for event in events) == 1
        assert db.scalar(
            select(JobAssignment).where(
                JobAssignment.job_id == uuid.UUID(job_id), JobAssignment.employee_id == recruiter2.id
            )
        ) is None


def test_public_careers_only_expose_published_jobs_without_salary_or_employee_fields(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "public-jobs-admin@example.com", EmployeeRole.ADMIN)
    headers = auth_headers(client, admin.email)
    draft = create_job(client, headers, title="Draft Hidden")
    published = create_job(client, headers, title="Visible Platform Engineer")
    publish(client, headers, published["id"])
    paused = create_job(client, headers, title="Paused Hidden")
    publish(client, headers, paused["id"])
    client.post(f"/api/v1/jobs/{paused['id']}/pause", headers=headers)
    closed = create_job(client, headers, title="Closed Hidden")
    publish(client, headers, closed["id"])
    client.post(f"/api/v1/jobs/{closed['id']}/close", headers=headers)
    archived = create_job(client, headers, title="Archived Hidden")
    publish(client, headers, archived["id"])
    client.post(f"/api/v1/jobs/{archived['id']}/close", headers=headers)
    client.post(f"/api/v1/jobs/{archived['id']}/archive", headers=headers)

    listing = client.get("/api/v1/public/jobs")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [published["id"]]
    item = listing.json()["items"][0]
    assert "salary_min" not in item and "salary_max" not in item
    assert "created_by_id" not in item and "updated_by_id" not in item
    assert "created_at" not in item and "updated_at" not in item
    assert "audit" not in str(item).lower() and "recruiter" not in str(item).lower()
    assert client.get(f"/api/v1/public/jobs/{published['id']}").status_code == 200
    for hidden in (draft, paused, closed, archived):
        assert client.get(f"/api/v1/public/jobs/{hidden['id']}").status_code == 404


def test_public_search_filters_experience_pagination_and_sort(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "filter-jobs-admin@example.com", EmployeeRole.ADMIN)
    headers = auth_headers(client, admin.email)
    first = create_job(
        client,
        headers,
        title="Backend Developer",
        department="Engineering",
        employment_type="Full-time",
        location="Bengaluru",
        work_mode="Hybrid",
        min_experience=2,
        max_experience=5,
    )
    second = create_job(
        client,
        headers,
        title="Data Analyst",
        department="Data",
        employment_type="Contract",
        location="Pune",
        work_mode="Remote",
        min_experience=5,
        max_experience=10,
    )
    publish(client, headers, first["id"])
    publish(client, headers, second["id"])

    filtered = client.get(
        "/api/v1/public/jobs?search=backend&department=engineering&employment_type=Full-time&location=bengaluru&work_mode=hybrid&experience_min=4&experience_max=6"
    )
    assert filtered.status_code == 200 and filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["id"] == first["id"]
    assert client.get("/api/v1/public/jobs?experience_min=8&experience_max=3").status_code == 422
    sorted_page = client.get("/api/v1/public/jobs?sort_by=title&order=asc&page=1&page_size=1")
    assert sorted_page.json()["total"] == 2
    assert len(sorted_page.json()["items"]) == 1
    assert sorted_page.json()["items"][0]["title"] == "Backend Developer"
    assert client.get("/api/v1/public/jobs?sort_by=salary_min").status_code == 422
