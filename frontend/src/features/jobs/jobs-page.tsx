"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { queryString, useRemote } from "@/lib/use-remote";
import type { Job, JobStatus, Page } from "@/lib/types";
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Field, Icon, LoadingState, PageHeader, SelectInput, TextInput, formatDate } from "@/components/ui";
import { JobForm } from "@/features/jobs/job-form";

const statuses: Array<"all" | JobStatus> = ["all", "draft", "published", "paused", "closed", "archived"];

export function JobsPage() {
  const { employee } = useAuth(); const [search, setSearch] = useState(""); const [status, setStatus] = useState("all"); const [page, setPage] = useState(1); const [createOpen, setCreateOpen] = useState(false);
  const query = queryString({ search, status: status === "all" ? undefined : status, page, page_size: 20, sort_by: "updated_at", order: "desc" });
  const resource = useRemote(() => api<Page<Job>>(`/jobs?${query}`), [query]);
  const canManage = employee?.role === "admin" || employee?.role === "hr_manager";
  return <>
    <PageHeader eyebrow="Recruitment" title="Jobs" description={employee?.role === "recruiter" ? "Positions assigned to you. Job details are read-only." : "Create roles, keep hiring teams aligned, and manage the approved job lifecycle."} action={canManage ? <Button onClick={() => setCreateOpen(true)}><Icon name="plus" size={16}/>Create job</Button> : undefined}/>
    <Card><div className="toolbar"><Field label="Search titles" className="field-search"><TextInput aria-label="Search job titles" value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Search job titles…"/></Field><Field label="Status"><SelectInput value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>{statuses.map((item) => <option key={item} value={item}>{item === "all" ? "All statuses" : item}</option>)}</SelectInput></Field><span className="toolbar-spacer"/><span className="result-count">{resource.data?.total ?? 0} jobs</span></div>
      {resource.loading ? <LoadingState label="Loading jobs"/> : resource.error ? <ErrorState message={resource.error} onRetry={resource.reload}/> : resource.data?.items.length ? <><div className="table-wrap"><table><thead><tr><th>Position</th><th>Department</th><th>Location</th><th>Deadline</th><th>Status</th><th/></tr></thead><tbody>{resource.data.items.map((job) => <tr key={job.id}><td><Link className="table-link" href={`/jobs/${job.id}`}>{job.title}</Link><span className="secondary-cell">{job.employment_type} · {job.work_mode}</span></td><td>{job.department}</td><td>{job.location}</td><td>{formatDate(job.application_deadline)}</td><td><Badge>{job.status}</Badge></td><td><Link aria-label={`Open ${job.title}`} className="table-link" href={`/jobs/${job.id}`}><Icon name="chevron" size={16}/></Link></td></tr>)}</tbody></table></div><Pagination page={page} size={resource.data.page_size} total={resource.data.total} onPage={setPage}/></> : <EmptyState title="No jobs match this view" detail="Try another search or create a role to begin a hiring workflow." action={canManage && <Button size="sm" onClick={() => setCreateOpen(true)}><Icon name="plus" size={14}/>Create job</Button>}/>}
    </Card>
    <Dialog open={createOpen} title="Create a job" description="New jobs begin as Draft. Publishing is a separate lifecycle action." onClose={() => setCreateOpen(false)} wide><div className="dialog-content"><JobForm onCancel={() => setCreateOpen(false)} onSaved={(job) => { setCreateOpen(false); resource.reload(); window.location.assign(`/jobs/${job.id}`); }}/></div></Dialog>
  </>;
}

function Pagination({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return <div className="pagination"><span>Showing {total ? (page - 1) * size + 1 : 0}–{Math.min(page * size, total)} of {total}</span><span className="pagination-actions"><Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button><Button size="sm" variant="secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button></span></div>;
}
