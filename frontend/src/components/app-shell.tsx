"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { EmployeeRole, Page, Notification } from "@/lib/types";
import { Icon, IconButton, LoadingState, initials } from "@/components/ui";

type NavItem = { href: string; label: string; icon: "grid" | "users" | "briefcase" | "file" | "calendar" | "bell"; roles: EmployeeRole[] };
const allRoles: EmployeeRole[] = ["admin", "hr_manager", "recruiter", "interviewer"];
const managerRoles: EmployeeRole[] = ["admin", "hr_manager"];
const navItems: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: "grid", roles: allRoles },
  { href: "/employees", label: "Employees", icon: "users", roles: ["admin"] },
  { href: "/jobs", label: "Jobs", icon: "briefcase", roles: ["admin", "hr_manager", "recruiter"] },
  { href: "/candidates", label: "Candidates", icon: "users", roles: managerRoles },
  { href: "/applications", label: "Applications", icon: "file", roles: ["admin", "hr_manager", "recruiter"] },
  { href: "/interviews", label: "Interviews", icon: "calendar", roles: allRoles },
  { href: "/offers", label: "Offers", icon: "file", roles: managerRoles },
  { href: "/notifications", label: "Notifications", icon: "bell", roles: allRoles },
];

export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const { employee, ready, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    if (ready && !employee) router.replace("/login");
  }, [employee, ready, router]);

  useEffect(() => {
    if (!employee) return;
    let alive = true;
    const loadUnread = async () => {
      try {
        const page = await api<Page<Notification>>("/notifications?unread=true&page=1&page_size=1");
        if (alive) setUnread(page.total);
      } catch { /* The page itself surfaces the API error; the shell remains usable. */ }
    };
    void loadUnread();
    const timer = window.setInterval(loadUnread, 60_000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [employee]);

  useEffect(() => {
    if (!mobileOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [mobileOpen]);

  if (!ready || !employee) return <div className="app-frame"><LoadingState label="Restoring your secure session"/></div>;
  const visibleNav = navItems.filter((item) => item.roles.includes(employee.role));
  const title = visibleNav.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`))?.label ?? "RecruitFlow";

  return <div className="app-frame">
    {mobileOpen && <button aria-label="Close navigation" className="mobile-scrim" onClick={() => setMobileOpen(false)}/>}
    <aside className={`sidebar ${collapsed ? "sidebar-collapsed" : ""} ${mobileOpen ? "sidebar-open" : ""}`}>
      <Link href="/dashboard" className="brand" aria-label="RecruitFlow home">
        <span className="brand-mark"><Icon name="briefcase" size={19}/></span>
        <span className="brand-copy"><span className="brand-name">RecruitFlow</span><span className="brand-caption">Talent workspace</span></span>
      </Link>
      <nav className="sidebar-nav" aria-label="Main navigation">
        <span className="nav-section-label">Workspace</span>
        {visibleNav.map((item) => {
          const active = pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(`${item.href}/`));
          return <Link key={item.href} href={item.href} title={collapsed ? item.label : undefined} aria-current={active ? "page" : undefined} onClick={() => setMobileOpen(false)} className={`nav-link ${active ? "active" : ""}`}>
          <span className="nav-icon"><Icon name={item.icon}/></span><span>{item.label}</span>{item.href === "/notifications" && unread > 0 && <span className="nav-count">{unread > 99 ? "99+" : unread}</span>}
        </Link>;
        })}
        <a className="nav-link" title={collapsed ? "Public careers" : undefined} href="/careers" target="_blank" rel="noreferrer"><span className="nav-icon"><Icon name="external"/></span><span>Public careers</span></a>
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-profile"><span className="avatar">{initials(employee.full_name || employee.email)}</span><span className="profile-copy"><strong>{employee.full_name || employee.email}</strong><span>{employee.role.replaceAll("_", " ")}</span></span></div>
        <button className="nav-link" title={collapsed ? "Sign out" : undefined} onClick={() => void logout()}><span className="nav-icon"><Icon name="logout"/></span><span>Sign out</span></button>
      </div>
    </aside>
    <div className={`workspace ${collapsed ? "workspace-collapsed" : ""}`}>
      <header className="topbar">
        <IconButton className="mobile-menu" label="Open navigation" icon="menu" onClick={() => setMobileOpen(true)}/>
        <IconButton className="collapse-button" label={collapsed ? "Expand navigation" : "Collapse navigation"} icon="menu" onClick={() => setCollapsed((value) => !value)}/>
        <div className="topbar-title"><div className="breadcrumbs">RecruitFlow <span aria-hidden="true">/</span> <strong>{title}</strong></div><div className="topbar-heading">{title}</div></div>
        <div className="topbar-actions"><Link className="topbar-notification" href="/notifications" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}><Icon name="bell"/>{unread > 0 && <span className="notification-count">{unread > 9 ? "9+" : unread}</span>}</Link><span className="topbar-avatar" title={`${employee.full_name || employee.email} · ${employee.role.replaceAll("_", " ")}`} aria-label={`Signed in as ${employee.full_name || employee.email}`}>{initials(employee.full_name || employee.email)}</span></div>
      </header>
      <main className="content">{children}</main>
    </div>
  </div>;
}

export function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
