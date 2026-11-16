"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, jsonBody } from "@/lib/api";
import { Button, Field, Icon, TextInput } from "@/components/ui";

export default function ResetPasswordPage() {
  const router = useRouter(); const [token, setToken] = useState(""); const [password, setPassword] = useState(""); const [confirm, setConfirm] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const [done, setDone] = useState(false);
  useEffect(() => { const params = new URLSearchParams(window.location.search); const value = params.get("token") || ""; setToken(value); window.history.replaceState(null, "", window.location.pathname); }, []);
  const submit = async (event: FormEvent) => { event.preventDefault(); if (password !== confirm) { setError("Passwords do not match."); return; } setBusy(true); setError(""); try { await api("/auth/password-reset/confirm", { method: "POST", authenticated: false, body: jsonBody({ token, new_password: password }) }); setDone(true); } catch (reason) { setError(reason instanceof Error ? reason.message : "Reset failed"); } finally { setBusy(false); } };
  return <AuthFrame title="Choose a new password" eyebrow="Secure reset" detail="Reset links work once and expire. Choose a strong password you do not use elsewhere.">{done ? <><div className="info-banner"><Icon name="check" size={16}/>Your password has been reset. Sign in with the new password.</div><Button className="auth-submit" onClick={() => router.push("/login")}>Continue to sign in</Button></> : <form className="auth-form" onSubmit={submit}>{error && <div className="auth-error" role="alert">{error}</div>}{!token && <div className="auth-error">This reset link is missing its token. Request a new one.</div>}<Field label="New password" hint="Use at least 12 characters."><TextInput type="password" autoComplete="new-password" minLength={12} required value={password} onChange={(event) => setPassword(event.target.value)}/></Field><Field label="Confirm password"><TextInput type="password" autoComplete="new-password" minLength={12} required value={confirm} onChange={(event) => setConfirm(event.target.value)}/></Field><Button disabled={busy || !token}>{busy && <span className="spinner"/>}Reset password</Button></form>}<div className="auth-links" style={{ marginTop: 20 }}><Link href="/login">Back to sign in</Link></div></AuthFrame>;
}

function AuthFrame({ title, eyebrow, detail, children }: { title: string; eyebrow: string; detail: string; children: React.ReactNode }) {
  return <div className="auth-page"><aside className="auth-aside"><Link className="brand" href="/login"><span className="brand-mark"><Icon name="briefcase" size={19}/></span><span className="brand-copy"><span className="brand-name">RecruitFlow</span><span className="brand-caption">Talent workspace</span></span></Link><div className="auth-copy"><p className="eyebrow">Account security</p><h1>Your account stays yours.</h1><p>Recovery is verified by an expiring email link.</p></div><div className="auth-note">Employee access only.</div></aside><main className="auth-main"><section className="auth-panel"><p className="eyebrow">{eyebrow}</p><h2>{title}</h2><p>{detail}</p>{children}</section></main></div>;
}
