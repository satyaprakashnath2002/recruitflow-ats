"use client";

import { FormEvent, useState } from "react";
import { api, jsonBody } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useRemote, queryString } from "@/lib/use-remote";
import type { Employee, EmployeeRole, EmployeeStatus, Page } from "@/lib/types";
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Field, Icon, LoadingState, PageHeader, SelectInput, TextInput, useToast, formatDate } from "@/components/ui";

export function EmployeesPage() {
  const { employee } = useAuth(); const [search, setSearch] = useState(""); const [role, setRole] = useState("all"); const [status, setStatus] = useState("all"); const [page, setPage] = useState(1); const [inviteOpen, setInviteOpen] = useState(false);
  const query = queryString({ search, role: role === "all" ? undefined : role, status: status === "all" ? undefined : status, page, page_size: 20 });
  const resource = useRemote(() => api<Page<Employee>>(`/employees?${query}`), [query]);
  const canManage = employee?.role === "admin";
  return <>
    <PageHeader eyebrow="Administration" title="Employees" description="Manage employee access and invitations. Employee deactivation follows backend safeguards, including the final active Admin and assigned interviewers." action={canManage && <Button onClick={() => setInviteOpen(true)}><Icon name="plus" size={15}/>Invite employee</Button>}/>
    <Card><div className="toolbar"><Field label="Search employees" className="field-search"><TextInput value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Name or work email"/></Field><Field label="Role"><SelectInput value={role} onChange={(event) => { setRole(event.target.value); setPage(1); }}><option value="all">All roles</option>{roles.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</SelectInput></Field><Field label="Status"><SelectInput value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}><option value="all">All statuses</option>{["active", "invited", "disabled"].map((item) => <option key={item}>{item}</option>)}</SelectInput></Field><span className="toolbar-spacer"/><span className="result-count">{resource.data?.total ?? 0} employees</span></div>
      {resource.loading ? <LoadingState label="Loading employee directory"/> : resource.error ? <ErrorState message={resource.error} onRetry={resource.reload}/> : resource.data?.items.length ? <><div className="table-wrap"><table><thead><tr><th>Employee</th><th>Role</th><th>Status</th><th>Last active</th><th>Added</th><th/></tr></thead><tbody>{resource.data.items.map((person) => <tr key={person.id}><td><span className="table-person"><span className="avatar">{(person.full_name || person.email).slice(0,1).toUpperCase()}</span><span><strong>{person.full_name || "Invitation pending"}</strong><small>{person.email}</small></span></span></td><td className="capitalize">{person.role.replaceAll("_", " ")}</td><td><Badge>{person.status}</Badge></td><td>{formatDate(person.last_login_at)}</td><td>{formatDate(person.created_at)}</td><td><EmployeeActions person={person} currentEmployeeId={employee?.id ?? ""} onUpdated={resource.reload}/></td></tr>)}</tbody></table></div><Pagination page={page} pageSize={resource.data.page_size} total={resource.data.total} onChange={setPage}/></> : <EmptyState title="No employees found" detail="Try a different search or invite a teammate to the workspace." action={<Button size="sm" onClick={() => setInviteOpen(true)}><Icon name="plus" size={14}/>Invite employee</Button>}/>}
    </Card>
    <InviteDialog open={inviteOpen} onClose={() => setInviteOpen(false)} onSent={() => { setInviteOpen(false); resource.reload(); }}/>
  </>;
}

const roles: EmployeeRole[] = ["admin", "hr_manager", "recruiter", "interviewer"];

function EmployeeActions({ person, currentEmployeeId, onUpdated }: { person: Employee; currentEmployeeId: string; onUpdated: () => void }) {
  const [open, setOpen] = useState(false); const [busy, setBusy] = useState(false); const [name, setName] = useState(person.full_name || ""); const [role, setRole] = useState(person.role); const [status, setStatus] = useState(person.status); const [error, setError] = useState(""); const toast = useToast();
  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    const payload: { full_name?: string; role?: EmployeeRole; status?: "active" | "disabled" } = {};
    if (name !== (person.full_name || "")) payload.full_name = name;
    if (role !== person.role) payload.role = role;
    if (status !== person.status && status !== "invited") payload.status = status;
    if (!Object.keys(payload).length) { setError("There are no changes to save."); setBusy(false); return; }
    try { await api<Employee>(`/employees/${person.id}`, { method: "PATCH", body: jsonBody(payload) }); toast("Employee access updated."); setOpen(false); onUpdated(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Employee could not be updated."); }
    finally { setBusy(false); }
  };
  return <><Button size="sm" variant="secondary" onClick={() => setOpen(true)}><Icon name="edit" size={13}/>Manage</Button><Dialog open={open} title="Employee access" description="Email and credentials cannot be changed here. Role and status changes are audited." onClose={() => setOpen(false)}><form className="dialog-content" onSubmit={save}>{error && <div className="auth-error">{error}</div>}<div className="employee-summary"><span className="avatar">{(person.full_name || person.email).slice(0,1).toUpperCase()}</span><div><strong>{person.full_name || "Invitation pending"}</strong><small>{person.email}</small></div></div><div className="form-grid"><Field label="Full name"><TextInput value={name} onChange={(e) => setName(e.target.value)} maxLength={200}/></Field><Field label="Role"><SelectInput value={role} disabled={person.id === currentEmployeeId} onChange={(e) => setRole(e.target.value as EmployeeRole)}>{roles.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</SelectInput>{person.id === currentEmployeeId && <span className="field-hint">You cannot change your own Admin role.</span>}</Field><Field label="Account status"><SelectInput value={status} onChange={(e) => setStatus(e.target.value as EmployeeStatus)}>{status === "invited" && <option value="invited" disabled>Invited</option>}<option value="active">Active</option><option value="disabled">Disabled</option></SelectInput></Field></div><div className="form-actions"><Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button disabled={busy}>{busy && <span className="spinner"/>}Save changes</Button></div></form></Dialog></>;
}

function InviteDialog({ open, onClose, onSent }: { open: boolean; onClose: () => void; onSent: () => void }) {
  const [email, setEmail] = useState(""); const [role, setRole] = useState<EmployeeRole>("recruiter"); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [sent, setSent] = useState(false); const toast = useToast();
  const submit = async (event: FormEvent) => { event.preventDefault(); setBusy(true); setError(""); try { await api("/employees/invitations", { method: "POST", body: jsonBody({ email, role }) }); setSent(true); toast("Invitation sent to the employee email address."); } catch (reason) { setError(reason instanceof Error ? reason.message : "Invitation could not be sent."); } finally { setBusy(false); } };
  return <Dialog open={open} title={sent ? "Invitation requested" : "Invite an employee"} description={sent ? "The backend sent an invitation if delivery is configured." : "The employee will set their name and password from the secure invitation link."} onClose={() => { setSent(false); setEmail(""); onClose(); }}><div className="dialog-content">{sent ? <><div className="info-banner"><Icon name="mail" size={16}/>Invitation details were sent to {email}. The raw invitation token is never shown here.</div><div className="form-actions"><Button onClick={() => { setSent(false); setEmail(""); onSent(); }}>Done</Button></div></> : <form onSubmit={submit}>{error && <div className="auth-error">{error}</div>}<div className="form-grid"><Field className="field-full" label="Work email"><TextInput type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="new.employee@company.com"/></Field><Field className="field-full" label="Role"><SelectInput value={role} onChange={(e) => setRole(e.target.value as EmployeeRole)}>{roles.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</SelectInput></Field></div><div className="form-actions"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button disabled={busy}>{busy && <span className="spinner"/>}Send invitation</Button></div></form>}</div></Dialog>;
}

function Pagination({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize)); return <div className="pagination"><span>Showing {total ? (page - 1) * pageSize + 1 : 0}–{Math.min(page * pageSize, total)} of {total}</span><span className="pagination-actions"><Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => onChange(page - 1)}>Previous</Button><Button size="sm" variant="secondary" disabled={page >= pages} onClick={() => onChange(page + 1)}>Next</Button></span></div>;
}
