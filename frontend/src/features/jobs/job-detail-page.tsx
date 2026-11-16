"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useRemote } from "@/lib/use-remote";
import type { Employee, Job, Page } from "@/lib/types";
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Field, Icon, LoadingState, PageHeader, SelectInput, TextInput, useToast, formatDate } from "@/components/ui";
import { JobForm } from "@/features/jobs/job-form";

type Assignment = { employee_id: string; full_name: string | null; email: string; assigned_at: string };
type AssignmentList = { job_id: string; recruiters: Assignment[] };
const nextActions: Record<Job["status"], Array<{ key: string; label: string; tone?: "primary" | "secondary" | "danger" }>> = {
  draft: [{ key: "publish", label: "Publish", tone: "primary" }],
  published: [{ key: "pause", label: "Pause", tone: "secondary" }, { key: "close", label: "Close", tone: "danger" }],
  paused: [{ key: "publish", label: "Republish", tone: "primary" }, { key: "close", label: "Close", tone: "danger" }],
  closed: [{ key: "archive", label: "Archive", tone: "secondary" }],
  archived: [],
};

export function JobDetailPage() {
  const params = useParams<{ job_id: string }>(); const id = params.job_id; const router = useRouter(); const { employee } = useAuth(); const toast = useToast();
  const [editOpen, setEditOpen] = useState(false); const [assigning, setAssigning] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const resource = useRemote(() => api<Job>(`/jobs/${id}`), [id]);
  const canManage = employee?.role === "admin" || employee?.role === "hr_manager";
  const assignmentResource = useRemote(() => canManage ? api<AssignmentList>(`/jobs/${id}/assignments`) : Promise.resolve({ job_id: id, recruiters: [] }), [id, canManage]);
  const employees = useRemote(() => canManage ? api<Page<Employee>>("/employees?role=recruiter&status=active&page=1&page_size=100") : Promise.resolve({ items: [], page: 1, page_size: 100, total: 0 }), [canManage]);
  const applications = useRemote(() => api<Page<unknown>>(`/applications?job_id=${encodeURIComponent(id)}&page=1&page_size=1`), [id]);
  const job = resource.data;

  const transition = async (action: string) => {
    if (!job) return;
    const target = action === "publish" ? "Published" : action === "pause" ? "Paused" : action === "close" ? "Closed" : "Archived";
    if (!window.confirm(`${action === "publish" ? "Publish" : action === "pause" ? "Pause" : action === "close" ? "Close" : "Archive"} “${job.title}”?`)) return;
    setBusy(true); setError("");
    try { await api<Job>(`/jobs/${id}/${action}`, { method: "POST" }); toast(`Job ${target.toLowerCase()}.`); resource.reload(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Job status could not be changed."); }
    finally { setBusy(false); }
  };
  const assign = async () => {
    if (!assigning) return;
    setBusy(true); setError("");
    try { await api(`/jobs/${id}/assignments/${assigning}`, { method: "PUT" }); toast("Recruiter assigned."); setAssigning(""); assignmentResource.reload(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Recruiter could not be assigned."); }
    finally { setBusy(false); }
  };
  const removeAssignment = async (employeeId: string) => {
    if (!window.confirm("Remove this Recruiter from the job?")) return;
    try { await api<void>(`/jobs/${id}/assignments/${employeeId}`, { method: "DELETE" }); toast("Recruiter removed."); assignmentResource.reload(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Recruiter could not be removed."); }
  };

  if (resource.loading) return <Card><LoadingState label="Loading job details"/></Card>;
  if (resource.error || !job) return <Card><ErrorState message={resource.error || "Job not found."} onRetry={resource.reload}/></Card>;

  return <>
    <Link className="breadcrumb-back" href="/jobs">← Back to jobs</Link>
    <PageHeader eyebrow={`${job.department} · ${job.employment_type}`} title={job.title} description={`${job.location} · ${job.work_mode}`} action={<div className="split-actions">{job.status === "published" && <Link className="button button-secondary button-md" href={`/careers/${job.id}`} target="_blank"><Icon name="external" size={14}/>Public listing</Link>}{canManage && <Button variant="secondary" onClick={() => setEditOpen(true)}><Icon name="edit" size={14}/>Edit job</Button>}{canManage && nextActions[job.status].map((action) => <Button key={action.key} variant={action.tone ?? "secondary"} disabled={busy} onClick={() => void transition(action.key)}>{busy && <span className="spinner"/>}{action.label}</Button>)}</div>}/>
    {error && <div className="auth-error" role="alert">{error}</div>}
    <div className="detail-grid"><div className="stack">
      <Card><header className="card-header"><div><h2 className="card-title">Job overview</h2><p className="card-subtitle">Internal job details</p></div><Badge>{job.status}</Badge></header><div className="card-body"><div className="detail-list"><Detail label="Department" value={job.department}/><Detail label="Employment type" value={job.employment_type}/><Detail label="Work mode" value={job.work_mode}/><Detail label="Location" value={job.location}/><Detail label="Experience" value={experience(job)}/><Detail label="Salary range" value={salary(job)}/><Detail label="Vacancies" value={String(job.vacancies)}/><Detail label="Application deadline" value={job.application_deadline ? formatDate(job.application_deadline) : "No deadline while Published"}/><Detail label="Applications" value={applications.data ? String(applications.data.total) : "Loading…"}/></div></div></Card>
      <Card><header className="card-header"><div><h2 className="card-title">Role description</h2><p className="card-subtitle">{job.description.length} characters</p></div></header><div className="card-body"><p className="description-text">{job.description}</p>{job.required_skills.length > 0 && <><h3 className="subheading">Required skills</h3><div className="pill-list">{job.required_skills.map((item) => <span className="pill" key={item}>{item}</span>)}</div></>}{job.qualifications.length > 0 && <><h3 className="subheading">Qualifications</h3><div className="pill-list">{job.qualifications.map((item) => <span className="pill" key={item}>{item}</span>)}</div></>}</div></Card>
    </div><div className="stack">
      {canManage && <Card><header className="card-header"><div><h2 className="card-title">Recruiter assignments</h2><p className="card-subtitle">Multiple Recruiters may be assigned to this job.</p></div></header>{assignmentResource.loading ? <LoadingState label="Loading assignments"/> : assignmentResource.error ? <ErrorState message={assignmentResource.error} onRetry={assignmentResource.reload}/> : <><div className="card-body assigned-list">{assignmentResource.data?.recruiters.length ? assignmentResource.data.recruiters.map((item) => <div className="assigned-row" key={item.employee_id}><span className="avatar">{(item.full_name || item.email).slice(0,1).toUpperCase()}</span><span className="profile-copy"><strong>{item.full_name || item.email}</strong><span>{item.email}</span></span><button className="text-link remove-link" onClick={() => void removeAssignment(item.employee_id)}>Remove</button></div>) : <p className="muted-small">No Recruiters are assigned.</p>}</div><div className="assign-controls"><Field label="Assign a Recruiter"><SelectInput value={assigning} onChange={(event) => setAssigning(event.target.value)}><option value="">Choose active Recruiter</option>{employees.data?.items.filter((person) => !assignmentResource.data?.recruiters.some((item) => item.employee_id === person.id)).map((person) => <option key={person.id} value={person.id}>{person.full_name || person.email}</option>)}</SelectInput></Field><Button size="sm" disabled={!assigning || busy} onClick={() => void assign()}><Icon name="plus" size={13}/>Assign</Button></div></>}</Card>}
      <Card className="card-pad"><div className="info-banner"><Icon name="shield" size={16}/><span>Public job listings show approved job fields only. Salary and employee assignment data remain private.</span></div></Card>
    </div></div>
    <Dialog open={editOpen} title="Edit job details" description="Status changes use the dedicated lifecycle actions." onClose={() => setEditOpen(false)} wide><div className="dialog-content"><JobForm initial={job} onCancel={() => setEditOpen(false)} onSaved={() => { setEditOpen(false); resource.reload(); }}/></div></Dialog>
  </>;
}

function Detail({ label, value }: { label: string; value: string }) { return <div className="detail-row"><span>{label}</span><strong>{value}</strong></div>; }
function experience(job: Job) { if (job.min_experience == null && job.max_experience == null) return "Not specified"; return `${job.min_experience ?? "0"}–${job.max_experience ?? "Any"} years`; }
function salary(job: Job) { if (job.salary_min == null && job.salary_max == null) return "Private; not listed publicly"; return `${job.salary_min ?? "—"} – ${job.salary_max ?? "—"} (private)`; }
