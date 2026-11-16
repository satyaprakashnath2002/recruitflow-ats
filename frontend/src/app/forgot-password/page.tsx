"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { api, jsonBody } from "@/lib/api";
import { Button, Field, Icon, TextInput } from "@/components/ui";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState(""); const [busy, setBusy] = useState(false); const [sent, setSent] = useState(false); const [error, setError] = useState("");
  const submit = async (event: FormEvent) => { event.preventDefault(); setBusy(true); setError(""); try { await api("/auth/password-reset/request", { method: "POST", authenticated: false, body: jsonBody({ email }) }); setSent(true); } catch (reason) { setError(reason instanceof Error ? reason.message : "Request failed"); } finally { setBusy(false); } };
  return <div className="auth-page"><aside className="auth-aside"><Link className="brand" href="/login"><span className="brand-mark"><Icon name="briefcase" size={19}/></span><span className="brand-copy"><span className="brand-name">RecruitFlow</span><span className="brand-caption">Talent workspace</span></span></Link><div className="auth-copy"><p className="eyebrow">Account access</p><h1>Get back to the work that matters.</h1><p>Password reset links are sent to active employee accounts without revealing account status.</p></div><div className="auth-note">For employee accounts only.</div></aside><main className="auth-main"><section className="auth-panel"><p className="eyebrow">Password recovery</p><h2>Forgot your password?</h2><p>Enter your work email. If the account can reset its password, we’ll send a secure link.</p>{sent ? <div className="info-banner"><Icon name="mail" size={16}/>If the account is eligible, a reset email is on its way.</div> : <form className="auth-form" onSubmit={submit}>{error && <div className="auth-error" role="alert">{error}</div>}<Field label="Work email"><TextInput type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com"/></Field><Button disabled={busy}>{busy && <span className="spinner"/>}Send reset link <Icon name="arrow" size={15}/></Button></form>}<div className="auth-links" style={{ marginTop: 20 }}><Link href="/login">Back to sign in</Link></div></section></main></div>;
}
