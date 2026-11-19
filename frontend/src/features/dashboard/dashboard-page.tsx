"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Application, EmployeeRole, InterviewRound, Job, Notification, Offer, Page } from "@/lib/types";
import { Badge, Button, Card, EmptyState, ErrorState, Icon, LoadingState, PageHeader, formatDate } from "@/components/ui";

interface Summary { jobs?: number; applications?: number; hires?: number; interviews?: number; offers?: number; notifications?: number; recent: Application[]; upcoming: InterviewRound[] }

export function DashboardPage() {
  const { employee } = useAuth(); const [data, setData] = useState<Summary | null>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const load = async () => {
      setLoading(true); setError("");
      try {
        const role = employee?.role as EmployeeRole;
        if (role === "interviewer") {
          const [interviews, notifications] = await Promise.all([
            api<Page<InterviewRound>>("/interviews/assigned?page=1&page_size=5"),
            api<Page<Notification>>("/notifications?unread=true&page=1&page_size=1"),
          ]);
          if (alive) setData({ interviews: interviews.total, notifications: notifications.total, recent: [], upcoming: interviews.items.filter((item) => item.status === "scheduled") });
        } else {
          const [jobs, apps, hired, interviews, offers, notifications] = await Promise.all([
            api<Page<Job>>("/jobs?page=1&page_size=1"),
            api<Page<Application>>("/applications?page=1&page_size=5"),
            api<Page<Application>>("/applications?stage=hired&page=1&page_size=1"),
            role === "admin" || role === "hr_manager" ? api<Page<InterviewRound>>("/applications?page=1&page_size=20").then(async (page) => {
              const rounds = await Promise.all(page.items.map((app) => api<Page<InterviewRound>>(`/applications/${app.id}/interview-rounds?page=1&page_size=5`).catch(() => null)));
              return rounds.flatMap((roundPage) => roundPage?.items ?? []).filter((round) => round.status === "scheduled").sort((a,b) => a.starts_at.localeCompare(b.starts_at)).slice(0,5);
            }) : Promise.resolve([] as InterviewRound[]),
            role === "admin" || role === "hr_manager" ? api<Page<Offer>>("/offers?page=1&page_size=1").catch(() => null) : Promise.resolve(null),
            api<Page<Notification>>("/notifications?unread=true&page=1&page_size=1"),
          ]);
          if (alive) setData({ jobs: jobs.total, applications: apps.total, hires: hired.total, interviews: interviews.length, offers: offers?.total, notifications: notifications.total, recent: apps.items, upcoming: interviews });
        }
      } catch (reason) { if (alive) setError(reason instanceof Error ? reason.message : "Dashboard data is unavailable."); }
      finally { if (alive) setLoading(false); }
    };
    if (employee) void load();
    return () => { alive = false; };
  }, [employee]);

  const role = employee?.role;
  const greeting = employee?.full_name || "there";
  const description = role === "interviewer" ? "Your assigned interviews and reminders, in one place." : role === "recruiter" ? "A clear view of the jobs and candidates assigned to you." : "A current view of your hiring workflow, based on live RecruitFlow records.";
  if (loading) return <><PageHeader eyebrow="Workspace" title={`Welcome back, ${greeting}`} description={description}/><Card><LoadingState label="Loading live recruitment data"/></Card></>;
  if (error) return <><PageHeader eyebrow="Workspace" title={`Welcome back, ${greeting}`} description={description}/><Card><ErrorState message={error}/></Card></>;
  const cards = role === "interviewer" ? [
    ["Assigned interviews", data?.interviews ?? 0, "Your active interview assignments", "calendar" as const],
    ["Unread notices", data?.notifications ?? 0, "Updates for your employee account", "bell" as const],
  ] : [
    ["Jobs in scope", data?.jobs ?? 0, role === "recruiter" ? "Jobs assigned to you" : "Jobs visible to your role", "briefcase" as const],
    ["Applications", data?.applications ?? 0, "Records currently visible to you", "file" as const],
    ["Hired", data?.hires ?? 0, "Applications at Hired stage", "users" as const],
    ...(role === "admin" || role === "hr_manager" ? [["Offers", data?.offers ?? 0, "Offer records visible to you", "file" as const]] : []),
  ];
  return <>
    <PageHeader eyebrow="Workspace overview" title={`Welcome back, ${greeting}`} description={description} action={<div className="dashboard-header-actions">{(role === "admin" || role === "hr_manager") && <Link className="button button-primary button-md" href="/jobs?create=true"><Icon name="plus" size={16}/>Create job</Link>}<Link className="button button-secondary button-md" href="/notifications"><Icon name="bell" size={15}/>{data?.notifications ? `${data.notifications} unread` : "Notifications"}</Link></div>}/>
    {cards.length > 0 && <div className="stat-grid">{cards.map(([label, value, foot, icon]) => <Card className="stat-card" key={String(label)}><span className="stat-label">{label}</span><span className="stat-mark"><Icon name={icon as "file"}/></span><div className="stat-value">{value}</div><span className="stat-foot">{foot}</span></Card>)}</div>}
    <div className={`dashboard-grid ${role === "interviewer" ? "dashboard-grid-interviewer" : ""}`}>
      {role !== "interviewer" && <Card className="dashboard-applications"><header className="card-header"><div><h2 className="card-title">Recent applications</h2><p className="card-subtitle">Latest records visible to your role</p></div><Link className="text-link" href="/applications">View all <Icon name="arrow" size={14}/></Link></header>{(data?.recent.length ?? 0) ? <div className="table-wrap"><table><thead><tr><th>Candidate</th><th>Location</th><th>Stage</th><th>Received</th></tr></thead><tbody>{data?.recent.map((app) => <tr key={app.id}><td><Link className="table-link" href={`/applications/${app.id}`}>{app.candidate.full_name}</Link><span className="secondary-cell">{app.candidate.email}</span></td><td className="primary-cell">{app.candidate.location || "—"}</td><td><Badge>{app.stage}</Badge></td><td>{formatDate(app.created_at)}</td></tr>)}</tbody></table></div> : <EmptyState title="No applications yet" detail="Applications will appear here when candidates apply to published jobs." action={<Link className="button button-secondary button-sm" href="/applications">Open applications</Link>}/>}</Card>}
      <Card className="dashboard-interviews"><header className="card-header"><div><h2 className="card-title">Upcoming interviews</h2><p className="card-subtitle">Scheduled interviews visible to your account</p></div><Link className="text-link" href="/interviews">View all <Icon name="arrow" size={14}/></Link></header>{data?.upcoming.length ? <div className="card-body dashboard-interview-list">{data.upcoming.map((interview) => <div className="dashboard-interview" key={interview.id}><span className="dashboard-interview-time">{formatDate(interview.starts_at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: interview.timezone_name })}</span><div><strong>{interview.candidate_full_name}</strong><span>{interview.job_title}</span></div></div>)}</div> : <EmptyState title="No upcoming interviews" detail="Scheduled interviews you can access will appear here."/>}</Card>
      <Card className="dashboard-actions"><header className="card-header"><div><h2 className="card-title">Quick actions</h2><p className="card-subtitle">Shortcuts to your available work</p></div></header><div className="card-body quick-links dashboard-action-grid">{role !== "interviewer" && <QuickLink href="/jobs" icon="briefcase" title="Review jobs" detail="Browse roles"/>}{role !== "interviewer" && <QuickLink href="/applications" icon="file" title="Applications" detail="Continue candidate review"/>}<QuickLink href="/interviews" icon="calendar" title="Interviews" detail={role === "interviewer" ? "Your assigned rounds" : "Scheduled rounds"}/>{role !== "recruiter" && role !== "interviewer" && <QuickLink href="/offers" icon="file" title="Offers" detail="Manage offer records"/>}<QuickLink href="/notifications" icon="bell" title="Notifications" detail="Reminders and updates"/></div></Card>
    </div>
  </>;
}

export function QuickLink({ href, icon, title, detail }: { href: string; icon: "briefcase" | "file" | "calendar" | "bell"; title: string; detail: string }) {
  return <Link className="quick-link" href={href}><span className="quick-icon"><Icon name={icon}/></span><span><strong>{title}</strong><small>{detail}</small></span><Icon name="chevron" size={15}/></Link>;
}
