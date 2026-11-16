"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";
import { Icon, Button, Field, TextInput } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const search = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(""); setBusy(true);
    try { await login(email, password); router.replace("/dashboard"); }
    catch (reason) { setError(reason instanceof ApiError && reason.status === 401 ? "Email or password was not recognized." : reason instanceof Error ? reason.message : "Sign in failed."); }
    finally { setBusy(false); }
  };
  return <form className="auth-form" onSubmit={submit}>
    {search.get("expired") && <div className="info-banner"><Icon name="shield" size={16}/>Your session ended. Sign in to continue.</div>}
    {error && <div className="auth-error" role="alert">{error}</div>}
    <Field label="Work email"><TextInput type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com"/></Field>
    <Field label="Password"><TextInput type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password"/></Field>
    <div className="auth-links"><span>Use your invited employee account</span><Link href="/forgot-password">Forgot password?</Link></div>
    <Button type="submit" disabled={busy}>{busy && <span className="spinner"/>}Sign in <Icon name="arrow" size={15}/></Button>
  </form>;
}

export default function LoginPage() {
  return <div className="auth-page"><aside className="auth-aside"><span className="brand"><span className="brand-mark"><Icon name="briefcase" size={19}/></span><span className="brand-copy"><span className="brand-name">RecruitFlow</span><span className="brand-caption">Talent workspace</span></span></span><div className="auth-copy"><p className="eyebrow">Recruitment, in sync</p><h1>Bring every hiring conversation into focus.</h1><p>Manage roles, candidate progress, interview feedback and offers in one secure workspace.</p></div><div className="auth-note">A private workspace for your recruitment team.</div></aside><main className="auth-main"><section className="auth-panel"><p className="eyebrow">Welcome back</p><h2>Sign in to RecruitFlow</h2><p>Use the email address and password associated with your employee invitation.</p><Suspense fallback={<div className="loading-state"><span className="spinner"/>Loading sign-in</div>}><LoginForm/></Suspense></section></main></div>;
}
