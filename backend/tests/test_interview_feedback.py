from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.applications import Application, Candidate
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.interviews import InterviewFeedback, InterviewFeedbackRevision, InterviewRound, InterviewerAssignment
from app.models.jobs import Job, JobAssignment

PASSWORD = "Interview feedback test password 77!"


def _employee(factory, name, role):
    with factory() as db:
        item = Employee(full_name=name, email=f"{uuid.uuid4()}@example.com", role=role,
                        status=EmployeeStatus.ACTIVE, password_hash=hash_password(PASSWORD))
        db.add(item)
        db.commit()
        db.refresh(item)
        return item


def _headers(client, employee):
    result = client.post("/api/v1/auth/login", json={"email": employee.email, "password": PASSWORD})
    assert result.status_code == 200, result.text
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


def _setup(factory, client):
    admin = _employee(factory, "Feedback Admin", EmployeeRole.ADMIN)
    hr = _employee(factory, "Feedback HR", EmployeeRole.HR_MANAGER)
    recruiter = _employee(factory, "Feedback Recruiter", EmployeeRole.RECRUITER)
    outsider = _employee(factory, "Feedback Outsider Recruiter", EmployeeRole.RECRUITER)
    interviewer = _employee(factory, "Feedback Interviewer", EmployeeRole.INTERVIEWER)
    peer = _employee(factory, "Feedback Peer", EmployeeRole.INTERVIEWER)
    with factory() as db:
        candidate = Candidate(full_name="Feedback Candidate", email=f"{uuid.uuid4()}@example.com")
        job = Job(title="Systems Engineer", department="Engineering", employment_type="Full-time",
                  location="Bengaluru", work_mode="Hybrid", description="Build systems.", vacancies=1,
                  status="published", created_by_id=admin.id, updated_by_id=admin.id)
        db.add_all([candidate, job])
        db.flush()
        application = Application(candidate_id=candidate.id, job_id=job.id, source="referral", stage="interview")
        db.add_all([application, JobAssignment(job_id=job.id, employee_id=recruiter.id, assigned_by_id=admin.id)])
        db.commit()
        app_id, job_id = application.id, job.id
    start = datetime.now(timezone.utc) + timedelta(days=10)
    created = client.post(f"/api/v1/applications/{app_id}/interview-rounds", headers=_headers(client, hr), json={
        "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
        "timezone_name": "Asia/Kolkata", "interviewer_ids": [str(interviewer.id), str(peer.id)],
    })
    assert created.status_code == 201, created.text
    round_id = uuid.UUID(created.json()["id"])
    with factory() as db:
        assignment_ids = {row.interviewer_employee_id: row.id for row in db.scalars(select(InterviewerAssignment).where(
            InterviewerAssignment.interview_round_id == round_id)).all()}
    return locals() | {"assignment_ids": assignment_ids, "round_id": round_id}


def _payload(**overrides):
    return {"rating": 4, "strengths": " Strong communication ", "areas_for_improvement": "More examples",
            "recommendation": "hire", **overrides}


def test_feedback_submit_edit_revisions_audit_and_role_visibility(api_client):
    client, factory, _ = api_client
    state = _setup(factory, client)
    path = f"/api/v1/interview-assignments/{state['assignment_ids'][state['interviewer'].id]}/feedback"
    own = _headers(client, state["interviewer"])
    peer_headers = _headers(client, state["peer"])
    hr = _headers(client, state["hr"])
    admin = _headers(client, state["admin"])
    recruiter = _headers(client, state["recruiter"])
    outsider = _headers(client, state["outsider"])

    assert client.get(f"/api/v1/interview-rounds/{state['round_id']}", headers=peer_headers).json()["feedback"] == []
    created = client.post(path, json=_payload(interviewer_employee_id=str(state["peer"].id)), headers=own)
    assert created.status_code == 422  # identity override and extra fields are forbidden
    created = client.post(path, json=_payload(), headers=own)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["strengths"] == "Strong communication"
    assert body["interviewer_employee_id"] == str(state["interviewer"].id)
    assert client.post(path, json=_payload(), headers=own).status_code == 409
    assert client.get(path, headers=own).json()["id"] == body["id"]

    detail = client.get(f"/api/v1/interview-rounds/{state['round_id']}", headers=peer_headers)
    assert detail.status_code == 200 and detail.json()["feedback"] == []
    assert client.get(f"/api/v1/interview-rounds/{state['round_id']}", headers=hr).json()["feedback"]
    assert client.get(f"/api/v1/interview-rounds/{state['round_id']}", headers=admin).status_code == 200
    assert len(client.get(f"/api/v1/interview-rounds/{state['round_id']}", headers=recruiter).json()["feedback"]) == 1
    assert client.get(f"/api/v1/interview-rounds/{state['round_id']}", headers=outsider).status_code == 404

    peer_path = f"/api/v1/interview-assignments/{state['assignment_ids'][state['peer'].id]}/feedback"
    assert client.post(peer_path, json=_payload(), headers=peer_headers).status_code == 201
    peer_detail = client.get(f"/api/v1/interview-rounds/{state['round_id']}", headers=own)
    assert len(peer_detail.json()["feedback"]) == 2

    edited = client.patch(path, json={"rating": 5, "strengths": "Clear systems thinking"}, headers=own)
    assert edited.status_code == 200, edited.text
    assert edited.json()["rating"] == 5
    with factory() as db:
        revisions = db.scalars(select(InterviewFeedbackRevision).where(
            InterviewFeedbackRevision.feedback_id == uuid.UUID(body["id"]))).all()
        assert len(revisions) == 1
        assert revisions[0].rating == 4 and revisions[0].strengths == "Strong communication"
        events = db.scalars(select(AuditLog).where(AuditLog.action.in_(
            ["interview.feedback_created", "interview.feedback_modified"]))).all()
        assert {event.action for event in events} == {"interview.feedback_created", "interview.feedback_modified"}
        serialized = " ".join(str(event.details) for event in events)
        assert "Strong communication" not in serialized
        assert "More examples" not in serialized
    assert client.post(path, json=_payload(), headers=hr).status_code == 403
    assert client.patch(path, json={"rating": 2}, headers=admin).status_code == 403


@pytest.mark.parametrize("payload", [
    {"rating": 0, "strengths": "x", "areas_for_improvement": "y", "recommendation": "hire"},
    {"rating": 6, "strengths": "x", "areas_for_improvement": "y", "recommendation": "hire"},
    {"rating": "3", "strengths": "x", "areas_for_improvement": "y", "recommendation": "hire"},
    {"rating": 3, "strengths": "  ", "areas_for_improvement": "y", "recommendation": "hire"},
    {"rating": 3, "strengths": "x", "areas_for_improvement": "y", "recommendation": "maybe"},
    {"rating": 3, "strengths": "x" * 5001, "areas_for_improvement": "y", "recommendation": "hire"},
    {"rating": 3, "strengths": "x", "areas_for_improvement": "y", "recommendation": "hire", "other": 1},
])
def test_feedback_payload_validation(api_client, payload):
    client, factory, _ = api_client
    state = _setup(factory, client)
    path = f"/api/v1/interview-assignments/{state['assignment_ids'][state['interviewer'].id]}/feedback"
    assert client.post(path, json=payload, headers=_headers(client, state["interviewer"])).status_code == 422


def test_feedback_text_limit_is_applied_after_whitespace_normalization(api_client):
    client, factory, _ = api_client
    state = _setup(factory, client)
    path = f"/api/v1/interview-assignments/{state['assignment_ids'][state['interviewer'].id]}/feedback"
    response = client.post(path, json=_payload(strengths=f"  {'x' * 5000}  "),
                           headers=_headers(client, state["interviewer"]))
    assert response.status_code == 201, response.text
    assert len(response.json()["strengths"]) == 5000


def test_feedback_patch_requires_valid_complete_result_and_exact_round_access(api_client):
    client, factory, _ = api_client
    state = _setup(factory, client)
    actor_headers = _headers(client, state["interviewer"])
    assignment_id = state["assignment_ids"][state["interviewer"].id]
    path = f"/api/v1/interview-assignments/{assignment_id}/feedback"
    assert client.patch(path, json={"strengths": "  "}, headers=actor_headers).status_code == 422
    assert client.post(path, json=_payload(), headers=actor_headers).status_code == 201

    # A second round on the same Application is not accessible through round-detail
    # unless the Interviewer has an active assignment to that exact round.
    start = datetime.now(timezone.utc) + timedelta(days=15)
    second = client.post(f"/api/v1/applications/{state['app_id']}/interview-rounds", headers=_headers(client, state["hr"]), json={
        "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
        "timezone_name": "Asia/Kolkata", "interviewer_ids": [str(state["peer"].id)],
    })
    assert second.status_code == 201, second.text
    assert client.get(f"/api/v1/interview-rounds/{second.json()['id']}", headers=actor_headers).status_code == 404
    assert client.get(f"/api/v1/interview-rounds/{state['round_id']}", headers=actor_headers).status_code == 200


def test_closed_and_removed_assignment_reject_mutations_but_round_can_complete_without_feedback(api_client):
    client, factory, _ = api_client
    state = _setup(factory, client)
    own = _headers(client, state["interviewer"])
    assignment_id = state["assignment_ids"][state["interviewer"].id]
    path = f"/api/v1/interview-assignments/{assignment_id}/feedback"
    completed = client.post(f"/api/v1/interview-rounds/{state['round_id']}/complete", headers=_headers(client, state["hr"]))
    assert completed.status_code == 200
    assert client.post(path, json=_payload(), headers=own).status_code == 409
    assert client.patch(path, json={"rating": 5}, headers=own).status_code == 404


def test_cancelled_round_rejects_feedback_writes_and_preserves_existing_records(api_client):
    client, factory, _ = api_client
    state = _setup(factory, client)
    own = _headers(client, state["interviewer"])
    assignment_id = state["assignment_ids"][state["interviewer"].id]
    path = f"/api/v1/interview-assignments/{assignment_id}/feedback"
    assert client.post(path, json=_payload(), headers=own).status_code == 201
    cancelled = client.post(f"/api/v1/interview-rounds/{state['round_id']}/cancel",
                            headers=_headers(client, state["hr"]))
    assert cancelled.status_code == 200
    assert client.patch(path, json={"rating": 5}, headers=own).status_code == 409
    with factory() as db:
        assert db.scalar(select(InterviewFeedback).where(
            InterviewFeedback.interview_round_id == state["round_id"])) is not None


def test_removed_assignment_cannot_write_and_does_not_delete_feedback_history(api_client):
    client, factory, _ = api_client
    state = _setup(factory, client)
    own = _headers(client, state["interviewer"])
    assignment_id = state["assignment_ids"][state["interviewer"].id]
    path = f"/api/v1/interview-assignments/{assignment_id}/feedback"
    assert client.post(path, json=_payload(), headers=own).status_code == 201
    removed = client.delete(
        f"/api/v1/interview-rounds/{state['round_id']}/interviewers/{state['interviewer'].id}",
        headers=_headers(client, state["hr"]),
    )
    assert removed.status_code == 204
    assert client.patch(path, json={"rating": 5}, headers=own).status_code == 409
    with factory() as db:
        assert db.get(InterviewerAssignment, assignment_id).removed_at is not None
        assert db.scalar(select(InterviewFeedback).where(
            InterviewFeedback.interview_round_id == state["round_id"])) is not None


def test_audit_failure_rolls_back_feedback_and_revision_writes(api_client, monkeypatch):
    from app.services import interview_feedback as feedback_service

    client, factory, _ = api_client
    state = _setup(factory, client)
    assignment_id = state["assignment_ids"][state["interviewer"].id]

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("simulated audit storage failure")

    monkeypatch.setattr(feedback_service, "_audit", fail_audit)
    with factory() as db:
        with pytest.raises(RuntimeError, match="simulated audit storage failure"):
            feedback_service.submit_feedback(db, assignment_id, db.get(Employee, state["interviewer"].id), _payload())
    with factory() as db:
        assert db.scalar(select(InterviewFeedback).where(
            InterviewFeedback.interview_round_id == state["round_id"])) is None

    monkeypatch.undo()
    with factory() as db:
        created = feedback_service.submit_feedback(
            db, assignment_id, db.get(Employee, state["interviewer"].id), _payload())
    monkeypatch.setattr(feedback_service, "_audit", fail_audit)
    with factory() as db:
        with pytest.raises(RuntimeError, match="simulated audit storage failure"):
            feedback_service.edit_feedback(
                db, assignment_id, db.get(Employee, state["interviewer"].id), {"rating": 5})
    with factory() as db:
        feedback = db.scalar(select(InterviewFeedback).where(
            InterviewFeedback.interview_round_id == state["round_id"]))
        assert feedback.rating == 4
        assert db.scalars(select(InterviewFeedbackRevision).where(
            InterviewFeedbackRevision.feedback_id == feedback.id)).all() == []

