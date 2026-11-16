"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { api, jsonBody } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useRemote, queryString } from "@/lib/use-remote";
import type { Application, ApplicationStage, EmployeeRole, Job, Page } from "@/lib/types";
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Field, Icon, LoadingState, PageHeader, SelectInput, TextInput, TextArea, useToast, formatDate } from "@/components/ui";

export function ApplicationsPage() {
  const { employee } = useAuth(); const [stage, setStage] = useState("all"); const [page, setPage] = useState(1); const [createOpen, setCreateOpen] = useState(false);
  const query = queryString({ stage: stage === "all" ? undefined : stage, page, page_size: 20 });
  const resource = useRemote(() => api<Page<Application>>(`/applications?${query}`), [query]);
  const canCreate = employee?.role !== "interviewer";
  return <>
    <PageHeader eyebrow="Hiring pipeline" title="Applications" description="Review candidate applications, keep stage changes on the approved workflow, and use dedicated notes and document actions." action={canCreate && <Button onClick={() => setCreateOpen(true)}><Icon name="plus" size={15}/>Add application</Button>}/>
    <Card><div className="toolbar"><Field label="Stage" className="field-search"><SelectInput value={stage} onChange={(event) => { setStage(event.target.value); setPage(1); }}><option value="all">All stages</option>{stages.map((item) => <option key={item} value={item}>{item}</option>)}</SelectInput></Field><span className="toolbar-spacer"/><span className="result-count">{resource.data?.total ?? 0} applications</span></div>
      {resource.loading ? <LoadingState label="Loading applications"/> : resource.error ? <ErrorState message={resource.error} onRetry={resource.reload}/> : resource.data?.items.length ? <ApplicationTable items={resource.data.items} page={page} pageSize={resource.data.page_size} total={resource.data.total} onPage={setPage}/> : <EmptyState title="No applications yet" detail="Applications appear here after a candidate submits or an authorized employee creates one." action={canCreate && <Button size="sm" onClick={() => setCreateOpen(true)}><Icon name="plus" size={14}/>Add application</Button>}/>}</Card>
    <Dialog open={createOpen} title="Add an application" description="The Candidate email is normalized and matched by the backend. Duplicate applications for a Job are rejected." onClose={() => setCreateOpen(false)} wide><div className="dialog-content"><ApplicationCreateForm onCancel={() => setCreateOpen(false)} onCreated={() => { setCreateOpen(false); resource.reload(); }}/></div></Dialog>
  </>;
}

function ApplicationTable({ items, page, pageSize, total, onPage }: { items: Application[]; page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const [titles, setTitles] = useState<Record<string, string>>({});
  // Resolve only visible page Job names through authorized internal endpoints.
  useEffect(() => {
    let active = true;
    void Promise.all([...new Set(items.map((item) => item.job_id))].map(async (id) => {
      try { const job = await api<Job>(`/jobs/${id}`); if (active) setTitles((current) => ({ ...current, [id]: job.title })); }
      catch { if (active) setTitles((current) => ({ ...current, [id]: "Position" })); }
    }));
    return () => { active = false; };
  }, [items]);
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <><div className="table-wrap"><table><thead><tr><th>Candidate</th><th>Position</th><th>Source</th><th>Stage</th><th>Received</th><th/></tr></thead><tbody>{items.map((app) => <tr key={app.id}><td><Link className="table-link" href={`/applications/${app.id}`}>{app.candidate.full_name}</Link><span className="secondary-cell">{app.candidate.email}</span></td><td>{titles[app.job_id] ?? "Loading position…"}</td><td className="capitalize">{app.source.replaceAll("_", " ")}</td><td><Badge>{app.stage}</Badge></td><td>{formatDate(app.created_at)}</td><td><Link className="table-link" href={`/applications/${app.id}`} aria-label={`Open application for ${app.candidate.full_name}`}><Icon name="chevron" size={16}/></Link></td></tr>)}</tbody></table></div><div className="pagination"><span>Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}</span><span className="pagination-actions"><Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button><Button size="sm" variant="secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button></span></div></>;
}

function ApplicationCreateForm({ onCreated, onCancel }: { onCreated: () => void; onCancel: () => void }) {
  const { employee } = useAuth(); const toast = useToast(); const [jobs, setJobs] = useState<Job[]>([]); const [jobsError, setJobsError] = useState(""); const [jobId, setJobId] = useState(""); const [source, setSource] = useState<"recruiter" | "referral">(employee?.role === "recruiter" ? "recruiter" : "referral"); const [name, setName] = useState(""); const [email, setEmail] = useState(""); const [phone, setPhone] = useState(""); const [location, setLocation] = useState(""); const [linkedin, setLinkedin] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useEffect(() => { let active = true; void api<Page<Job>>("/jobs?status=published&page=1&page_size=100").then((result) => { if (active) setJobs(result.items); }).catch((reason) => { if (active) setJobsError(reason instanceof Error ? reason.message : "Jobs could not be loaded."); }); return () => { active = false; }; }, []);
  const submit = async (event: FormEvent) => { event.preventDefault(); if (!jobId) { setError("Choose a published job."); return; } setBusy(true); setError(""); try { await api<Application>("/applications", { method: "POST", body: jsonBody({ job_id: jobId, source, candidate: { full_name: name, email, phone: phone || null, location: location || null, linkedin_url: linkedin || null } }) }); toast("Application created at Applied stage."); onCreated(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Application could not be created."); } finally { setBusy(false); } };
  return <form onSubmit={submit}>{error && <div className="auth-error" role="alert">{error}</div>}{jobsError && <div className="auth-error" role="alert">{jobsError}</div>}<div className="form-grid"><Field className="field-full" label="Published job"><SelectInput required value={jobId} onChange={(e) => setJobId(e.target.value)}><option value="">Choose a job</option>{jobs.map((job) => <option key={job.id} value={job.id}>{job.title} · {job.location}</option>)}</SelectInput><span className="field-hint">A deadline-free Published Job accepts applications while it remains Published. Employee-assisted entries are audited.</span></Field><Field label="Candidate full name"><TextInput required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name"/></Field><Field label="Candidate email"><TextInput required type="email" maxLength={320} value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email"/></Field><Field label="Source"><SelectInput value={source} onChange={(e) => setSource(e.target.value as "recruiter" | "referral")}><option value="recruiter">Recruiter</option><option value="referral">Referral</option></SelectInput></Field><Field label="Phone (optional)"><TextInput type="tel" value={phone} onChange={(e) => setPhone(e.target.value)}/></Field><Field label="Location (optional)"><TextInput value={location} onChange={(e) => setLocation(e.target.value)}/></Field><Field label="LinkedIn URL (optional)"><TextInput type="url" value={linkedin} onChange={(e) => setLinkedin(e.target.value)}/></Field></div><div className="form-actions"><Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button><Button disabled={busy || !jobs.length}>{busy && <span className="spinner"/>}Create application</Button></div></form>;
}

const stages: ApplicationStage[] = ["applied", "screening", "shortlisted", "interview", "selected", "offer", "hired", "rejected", "withdrawn"];
