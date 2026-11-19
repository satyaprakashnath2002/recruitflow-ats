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
  const [showPassword, setShowPassword] = useState(false);
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
    <Field label="Work email" className="auth-field"><TextInput type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com"/></Field>
    <Field label="Password" className="auth-field"><TextInput type={showPassword ? "text" : "password"} autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password"/><button type="button" className="password-toggle" aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword} onClick={() => setShowPassword((shown) => !shown)}><svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg></button></Field>
    <div className="auth-links"><span></span><Link href="/forgot-password">Forgot Password?</Link></div>
    <Button type="submit" disabled={busy}>{busy && <span className="spinner"/>}Sign in</Button>
  </form>;
}

export default function LoginPage() {
  return <main className="auth-page">
    <aside className="auth-aside">
      <Link className="brand" href="/login"><span className="brand-mark"><Icon name="briefcase" size={19}/></span><span className="brand-copy"><span className="brand-name">RecruitFlow</span><span className="brand-caption">Talent workspace</span></span></Link>
      <div className="auth-copy"><p className="eyebrow">RecruitFlow workspace</p><h1>A clearer way to move great people forward.</h1><p>Sign in to manage your hiring work, keep interviews on track, and make thoughtful decisions with your team.</p></div>
      <div className="auth-note">Secure access for your recruitment team.</div>
    </aside>
    <section className="auth-main"><div className="auth-panel">
      <div className="auth-heading"><p className="eyebrow">Welcome back</p><h2>Sign in to RecruitFlow</h2><p>Use the work email linked to your employee invitation.</p></div>
      <Suspense fallback={<div className="loading-state"><span className="spinner"/>Loading sign-in</div>}><LoginForm/></Suspense>
    </div></section>
  </main>;
}
