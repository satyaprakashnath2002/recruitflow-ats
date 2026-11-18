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
    <Field label="Work email" className="auth-field"><span className="auth-field-icon"><Icon name="users" size={17}/></span><TextInput type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="Work email"/></Field>
    <Field label="Password" className="auth-field"><span className="auth-field-icon"><Icon name="shield" size={17}/></span><TextInput type={showPassword ? "text" : "password"} autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password"/><button type="button" className="password-toggle" aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword} onClick={() => setShowPassword((shown) => !shown)}><svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg></button></Field>
    <div className="auth-links"><span></span><Link href="/forgot-password">Forgot Password?</Link></div>
    <Button type="submit" disabled={busy}>{busy && <span className="spinner"/>}Sign in</Button>
  </form>;
}

export default function LoginPage() {
  return <main className="auth-page"><div className="auth-card"><section className="auth-panel">
    <div className="auth-heading"><p className="auth-welcome">Login</p><p className="auth-intro">Sign in to your account</p></div>
    <Suspense fallback={<div className="loading-state"><span className="spinner"/>Loading sign-in</div>}><LoginForm/></Suspense>
    <p className="auth-account-note">Use the work email linked to your employee invitation.</p>
  </section></div></main>;
}
