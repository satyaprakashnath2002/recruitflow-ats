from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.applications import Application, Candidate
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.interviews import InterviewFeedback, InterviewRound, InterviewerAssignment
from app.models.jobs import Job, JobAssignment

PASSWORD = "Interview scheduling test password 77!"


def employee(factory, name: str, role: EmployeeRole):
    with factory() as db:
        item = Employee(full_name=name, email=f"{name.lower().replace(' ', '.')}@example.com",
                        role=role, status=EmployeeStatus.ACTIVE, password_hash=hash_password(PASSWORD))
        db.add(item)
        db.commit()
        db.refresh(item)
        return item


def headers(client, person):
    result = client.post("/api/v1/auth/login", json={"email": person.email, "password": PASSWORD})
    assert result.status_code == 200, result.text
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


def fixture_application(factory, admin, recruiter):
    with factory() as db:
        candidate = Candidate(full_name="Private Candidate", email=f"{uuid.uuid4()}@example.test")
        job = Job(title="Platform Engineer", department="Engineering", employment_type="Full-time",
                  location="Bengaluru", work_mode="Hybrid", description="Build systems.", vacancies=1,
                  status="published", created_by_id=admin.id, updated_by_id=admin.id)
        db.add_all([candidate, job])
        db.flush()
        app = Application(candidate_id=candidate.id, job_id=job.id, source="referral", stage="interview")
        db.add(app)
        db.add(JobAssignment(job_id=job.id, employee_id=recruiter.id, assigned_by_id=admin.id))
        db.commit()
        return app.id, job.id


def schedule_payload(start: datetime, interviewer_ids=None):
    return {"starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
            "timezone_name": "Asia/Kolkata", "interviewer_ids": interviewer_ids or []}


def test_hr_and_assigned_recruiter_can_schedule_with_audit(api_client):
    client, factory, _ = api_client
    admin = employee(factory, "Scheduling Admin", EmployeeRole.ADMIN)
    hr = employee(factory, "Scheduling HR", EmployeeRole.HR_MANAGER)
    recruiter = employee(factory, "Scheduling Recruiter", EmployeeRole.RECRUITER)
    interviewer = employee(factory, "Scheduling Interviewer", EmployeeRole.INTERVIEWER)
    application_id, _ = fixture_application(factory, admin, recruiter)
    start = datetime.now(timezone.utc) + timedelta(days=3)

    response = client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                           json=schedule_payload(start, [str(interviewer.id)]), headers=headers(client, hr))
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "scheduled"
    assert result["candidate_full_name"] == "Private Candidate"
    assert result["interviewers"][0]["interviewer_employee_id"] == str(interviewer.id)
    with factory() as db:
        actions = db.scalars(select(AuditLog.action).where(AuditLog.target_application_id == application_id)).all()
        assert set(actions) == {"interview.round_created", "interview.scheduled", "interview.interviewer_assigned"}

    recruiter_response = client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                                     json=schedule_payload(start + timedelta(hours=2)),
                                     headers=headers(client, recruiter))
    assert recruiter_response.status_code == 201, recruiter_response.text
    assert client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                       json=schedule_payload(start + timedelta(hours=4)), headers=headers(client, admin)).status_code == 403


def test_assignment_scope_conflicts_lifecycle_and_terminal_lock(api_client):
    client, factory, _ = api_client
    admin = employee(factory, "Conflict Admin", EmployeeRole.ADMIN)
    recruiter = employee(factory, "Conflict Recruiter", EmployeeRole.RECRUITER)
    interviewer = employee(factory, "Conflict Interviewer", EmployeeRole.INTERVIEWER)
    extra_interviewer = employee(factory, "Conflict Extra Interviewer", EmployeeRole.INTERVIEWER)
    replacement_interviewer = employee(factory, "Conflict Replacement Interviewer", EmployeeRole.INTERVIEWER)
    app_one, _ = fixture_application(factory, admin, recruiter)
    app_two, _ = fixture_application(factory, admin, recruiter)
    recruiter_headers = headers(client, recruiter)
    start = datetime.now(timezone.utc) + timedelta(days=5)
    first = client.post(f"/api/v1/applications/{app_one}/interview-rounds",
                        json=schedule_payload(start, [str(interviewer.id)]), headers=recruiter_headers)
    assert first.status_code == 201, first.text
    adjacent = client.post(f"/api/v1/applications/{app_two}/interview-rounds",
                           json=schedule_payload(start + timedelta(hours=1), [str(interviewer.id)]),
                           headers=recruiter_headers)
    assert adjacent.status_code == 201, adjacent.text
    assigned = client.put(
        f"/api/v1/interview-rounds/{first.json()['id']}/interviewers/{extra_interviewer.id}",
        headers=recruiter_headers,
    )
    assert assigned.status_code == 201, assigned.text
    duplicate_assignment = client.put(
        f"/api/v1/interview-rounds/{first.json()['id']}/interviewers/{extra_interviewer.id}",
        headers=recruiter_headers,
    )
    assert duplicate_assignment.status_code == 409
    removed = client.delete(
        f"/api/v1/interview-rounds/{first.json()['id']}/interviewers/{extra_interviewer.id}",
        headers=recruiter_headers,
    )
    assert removed.status_code == 204
    # M6-D12 keeps the original episode, feedback and audit history; a re-add is
    # rejected even though this round remains Scheduled, while another employee is allowed.
    with factory.begin() as db:
        assignment = db.scalar(select(InterviewerAssignment).where(
            InterviewerAssignment.interview_round_id == uuid.UUID(first.json()["id"]),
            InterviewerAssignment.interviewer_employee_id == extra_interviewer.id,
        ))
        assert assignment is not None and assignment.removed_at is not None
        assert assignment.removed_by_id == recruiter.id and assignment.assigned_at is not None
        db.add(InterviewFeedback(
            interview_round_id=assignment.interview_round_id,
            interviewer_employee_id=assignment.interviewer_employee_id,
            rating=4, strengths="Clear communication", areas_for_improvement="More examples",
            recommendation="hire",
        ))
    readd = client.put(
        f"/api/v1/interview-rounds/{first.json()['id']}/interviewers/{extra_interviewer.id}",
        headers=recruiter_headers,
    )
    assert readd.status_code == 409
    replacement = client.put(
        f"/api/v1/interview-rounds/{first.json()['id']}/interviewers/{replacement_interviewer.id}",
        headers=recruiter_headers,
    )
    assert replacement.status_code == 201, replacement.text
    with factory() as db:
        assert db.scalar(select(InterviewFeedback).where(
            InterviewFeedback.interview_round_id == uuid.UUID(first.json()["id"]),
            InterviewFeedback.interviewer_employee_id == extra_interviewer.id,
        )) is not None
        assert db.scalar(select(InterviewerAssignment).where(
            InterviewerAssignment.interview_round_id == uuid.UUID(first.json()["id"]),
            InterviewerAssignment.interviewer_employee_id == extra_interviewer.id,
        )) is not None
        assert db.scalar(select(AuditLog).where(
            AuditLog.action == "interview.interviewer_removed",
            AuditLog.target_application_id == app_one,
        )) is not None
    overlap = client.post(f"/api/v1/applications/{app_two}/interview-rounds",
                          json=schedule_payload(start + timedelta(minutes=30), [str(interviewer.id)]),
                          headers=recruiter_headers)
    assert overlap.status_code == 409
    round_id = first.json()["id"]
    assert client.patch(f"/api/v1/interview-rounds/{round_id}", json={"status": "completed"},
                        headers=recruiter_headers).status_code == 200
    assert client.patch(f"/api/v1/interview-rounds/{round_id}", json={"status": "cancelled"},
                        headers=recruiter_headers).status_code == 400
    assert client.put(f"/api/v1/interview-rounds/{round_id}/interviewers/{interviewer.id}",
                      headers=recruiter_headers).status_code == 400
    assert client.patch(f"/api/v1/interview-rounds/{adjacent.json()['id']}",
                        json={"status": "cancelled"}, headers=recruiter_headers).status_code == 200
    with factory() as db:
        audit_actions = set(db.scalars(select(AuditLog.action)).all())
        assert "interview.interviewer_removed" in audit_actions
        assert "interview.completed" in audit_actions
        assert "interview.cancelled" in audit_actions


def test_interviewer_projection_is_scoped_and_scheduling_inputs_validate(api_client):
    client, factory, _ = api_client
    admin = employee(factory, "Projection Admin", EmployeeRole.ADMIN)
    recruiter = employee(factory, "Projection Recruiter", EmployeeRole.RECRUITER)
    interviewer = employee(factory, "Projection Interviewer", EmployeeRole.INTERVIEWER)
    outsider = employee(factory, "Projection Outsider", EmployeeRole.INTERVIEWER)
    application_id, _ = fixture_application(factory, admin, recruiter)
    start = datetime.now(timezone.utc) + timedelta(days=8)
    created = client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                          json=schedule_payload(start, [str(interviewer.id)]), headers=headers(client, recruiter))
    assert created.status_code == 201, created.text
    round_id = created.json()["id"]
    outsider_headers = headers(client, outsider)
    assert client.get(f"/api/v1/interview-rounds/{round_id}", headers=outsider_headers).status_code == 404
    assigned = client.get("/api/v1/interviews/assigned", headers=headers(client, interviewer))
    assert assigned.status_code == 200
    projection = assigned.json()["items"][0]
    assert projection["candidate_full_name"] == "Private Candidate"
    assert "email" not in projection and "phone" not in projection and "job_id" not in projection
    assert "application_id" not in projection
    # Assigned Interviewers use dedicated routes, never application-scoped listings.
    denied_app_scope = client.get(f"/api/v1/applications/{application_id}/interview-rounds",
                                 headers=headers(client, interviewer))
    assert denied_app_scope.status_code == 403
    assert denied_app_scope.json()["error"]["status"] == "dedicated_endpoint_required"
    assert client.get(f"/api/v1/interview-rounds/{round_id}",
                      headers=headers(client, interviewer)).status_code == 200
    invalid_naive = {"starts_at": start.replace(tzinfo=None).isoformat(),
                     "ends_at": (start + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
                     "timezone_name": "Asia/Kolkata"}
    assert client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                       json=invalid_naive, headers=headers(client, recruiter)).status_code == 422
    duplicate = schedule_payload(start + timedelta(days=2), [str(interviewer.id), str(interviewer.id)])
    assert client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                       json=duplicate, headers=headers(client, recruiter)).status_code == 422


def test_reschedule_rejects_conflicts_and_records_previous_schedule(api_client):
    client, factory, _ = api_client
    admin = employee(factory, "Reschedule Admin", EmployeeRole.ADMIN)
    hr = employee(factory, "Reschedule HR", EmployeeRole.HR_MANAGER)
    recruiter = employee(factory, "Reschedule Recruiter", EmployeeRole.RECRUITER)
    interviewer = employee(factory, "Reschedule Interviewer", EmployeeRole.INTERVIEWER)
    app_one, _ = fixture_application(factory, admin, recruiter)
    app_two, _ = fixture_application(factory, admin, recruiter)
    start = datetime.now(timezone.utc) + timedelta(days=12)
    h = headers(client, hr)
    one = client.post(f"/api/v1/applications/{app_one}/interview-rounds",
                      json=schedule_payload(start, [str(interviewer.id)]), headers=h).json()
    client.post(f"/api/v1/applications/{app_two}/interview-rounds",
                json=schedule_payload(start + timedelta(hours=2), [str(interviewer.id)]), headers=h)
    overlap_payload = schedule_payload(start + timedelta(hours=1, minutes=30))
    overlap_payload.pop("interviewer_ids")
    overlap = client.patch(f"/api/v1/interview-rounds/{one['id']}", json=overlap_payload, headers=h)
    assert overlap.status_code == 409
    moved_payload = schedule_payload(start - timedelta(hours=2))
    moved_payload.pop("interviewer_ids")
    moved = client.patch(f"/api/v1/interview-rounds/{one['id']}", json=moved_payload, headers=h)
    assert moved.status_code == 200
    with factory() as db:
        event = db.scalar(select(AuditLog).where(AuditLog.action == "interview.rescheduled"))
        assert event is not None and "previous" in event.details


def test_interview_read_write_permissions_and_interview_stage_prerequisite(api_client):
    client, factory, _ = api_client
    admin = employee(factory, "Access Admin", EmployeeRole.ADMIN)
    hr = employee(factory, "Access HR", EmployeeRole.HR_MANAGER)
    recruiter = employee(factory, "Access Recruiter", EmployeeRole.RECRUITER)
    unassigned = employee(factory, "Access Unassigned Recruiter", EmployeeRole.RECRUITER)
    interviewer = employee(factory, "Access Interviewer", EmployeeRole.INTERVIEWER)
    application_id, _ = fixture_application(factory, admin, recruiter)
    start = datetime.now(timezone.utc) + timedelta(days=20)
    hr_headers = headers(client, hr)
    created = client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                          json=schedule_payload(start), headers=hr_headers)
    assert created.status_code == 201, created.text
    round_id = created.json()["id"]
    admin_headers = headers(client, admin)
    assert client.get(f"/api/v1/interview-rounds/{round_id}", headers=admin_headers).status_code == 200
    assert client.patch(f"/api/v1/interview-rounds/{round_id}",
                        json={key: value for key, value in schedule_payload(start + timedelta(hours=2)).items()
                              if key != "interviewer_ids"}, headers=admin_headers).status_code == 403
    unassigned_headers = headers(client, unassigned)
    assert client.get(f"/api/v1/interview-rounds/{round_id}", headers=unassigned_headers).status_code == 404
    assert client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                       json=schedule_payload(start + timedelta(days=1)), headers=unassigned_headers).status_code == 404
    interviewer_headers = headers(client, interviewer)
    assert client.get(f"/api/v1/interview-rounds/{round_id}", headers=interviewer_headers).status_code == 404
    assert client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                       json=schedule_payload(start + timedelta(days=2)), headers=interviewer_headers).status_code == 403
    assert client.get(f"/api/v1/interview-rounds/{round_id}").status_code == 401

    with factory.begin() as db:
        db.get(Application, application_id).stage = "screening"
    invalid_stage = client.post(f"/api/v1/applications/{application_id}/interview-rounds",
                               json=schedule_payload(start + timedelta(days=3)), headers=hr_headers)
    assert invalid_stage.status_code == 400
