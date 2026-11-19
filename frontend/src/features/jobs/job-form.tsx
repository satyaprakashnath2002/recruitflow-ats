"use client";

import { FormEvent, useState } from "react";
import { api, jsonBody } from "@/lib/api";
import type { Job } from "@/lib/types";
import { Button, Field, TextArea, TextInput, useToast } from "@/components/ui";

const blank = {
  title: "", department: "", employment_type: "Full-time", location: "", work_mode: "Hybrid",
  min_experience: "", max_experience: "", salary_min: "", salary_max: "", description: "",
  required_skills: "", qualifications: "", vacancies: "1", application_deadline: "",
};

function companyToday() {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function JobForm({ initial, onSaved, onCancel }: { initial?: Job; onSaved: (job: Job) => void; onCancel?: () => void }) {
  const [form, setForm] = useState(() => initial ? {
    title: initial.title, department: initial.department, employment_type: initial.employment_type,
    location: initial.location, work_mode: initial.work_mode, min_experience: initial.min_experience?.toString() ?? "",
    max_experience: initial.max_experience?.toString() ?? "", salary_min: initial.salary_min?.toString() ?? "",
    salary_max: initial.salary_max?.toString() ?? "", description: initial.description,
    required_skills: initial.required_skills.join("\n"), qualifications: initial.qualifications.join("\n"),
    vacancies: String(initial.vacancies), application_deadline: initial.application_deadline ?? "",
  } : blank);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const toast = useToast();
  const set = (key: keyof typeof blank, value: string) => setForm((current) => ({ ...current, [key]: value }));
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError("");
    const number = (value: string) => value === "" ? null : Number(value);
    const required: Array<[string, string]> = [
      ["Job title", form.title], ["Department", form.department], ["Employment type", form.employment_type],
      ["Location", form.location], ["Work mode", form.work_mode], ["Description", form.description],
    ];
    const missing = required.find(([, value]) => !value.trim());
    if (missing) { setError(`${missing[0]} is required.`); return; }
    const minExperience = number(form.min_experience);
    const maxExperience = number(form.max_experience);
    const minSalary = number(form.salary_min);
    const maxSalary = number(form.salary_max);
    if (minExperience !== null && maxExperience !== null && minExperience > maxExperience) {
      setError("Minimum experience cannot exceed maximum experience."); return;
    }
    if (minSalary !== null && maxSalary !== null && minSalary > maxSalary) {
      setError("Minimum salary cannot exceed maximum salary."); return;
    }
    if (form.application_deadline && form.application_deadline <= companyToday()) {
      setError("Application deadline must be a future date in Asia/Kolkata."); return;
    }
    const payload = {
      title: form.title.trim(), department: form.department.trim(), employment_type: form.employment_type.trim(),
      location: form.location.trim(), work_mode: form.work_mode.trim(), description: form.description.trim(),
      vacancies: Number(form.vacancies), min_experience: minExperience, max_experience: maxExperience,
      salary_min: minSalary, salary_max: maxSalary,
      required_skills: form.required_skills.split("\n").map((value) => value.trim()).filter(Boolean),
      qualifications: form.qualifications.split("\n").map((value) => value.trim()).filter(Boolean),
      application_deadline: form.application_deadline || null,
    };
    setBusy(true);
    try {
      const job = initial
        ? await api<Job>(`/jobs/${initial.id}`, { method: "PATCH", body: jsonBody(payload) })
        : await api<Job>("/jobs", { method: "POST", body: jsonBody(payload) });
      toast(initial ? "Job details saved." : "Job created as Draft."); onSaved(job);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Job could not be saved."); }
    finally { setBusy(false); }
  };
  return <form onSubmit={submit}>
    {error && <div className="auth-error" role="alert">{error}</div>}
    <div className="form-grid">
      <Field label="Job title"><TextInput required maxLength={200} value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="Senior Product Designer"/></Field>
      <Field label="Department"><TextInput required maxLength={120} value={form.department} onChange={(e) => set("department", e.target.value)} placeholder="Product & Design"/></Field>
      <Field label="Employment type"><TextInput required maxLength={50} value={form.employment_type} onChange={(e) => set("employment_type", e.target.value)} placeholder="Full-time"/></Field>
      <Field label="Location"><TextInput required maxLength={200} value={form.location} onChange={(e) => set("location", e.target.value)} placeholder="Bengaluru, India"/></Field>
      <Field label="Work mode"><TextInput required maxLength={50} value={form.work_mode} onChange={(e) => set("work_mode", e.target.value)} placeholder="Hybrid"/></Field>
      <Field label="Vacancies"><TextInput required type="number" min="1" step="1" value={form.vacancies} onChange={(e) => set("vacancies", e.target.value)}/></Field>
      <Field label="Minimum experience (years)"><TextInput type="number" min="0" step="0.25" value={form.min_experience} onChange={(e) => set("min_experience", e.target.value)} placeholder="Optional"/></Field>
      <Field label="Maximum experience (years)"><TextInput type="number" min="0" step="0.25" value={form.max_experience} onChange={(e) => set("max_experience", e.target.value)} placeholder="Optional"/></Field>
      <Field label="Minimum salary"><TextInput type="number" min="0" step="0.01" value={form.salary_min} onChange={(e) => set("salary_min", e.target.value)} placeholder="Private to employees"/></Field>
      <Field label="Maximum salary"><TextInput type="number" min="0" step="0.01" value={form.salary_max} onChange={(e) => set("salary_max", e.target.value)} placeholder="Private to employees"/></Field>
      <Field label="Application deadline" hint="Leave blank to accept applications while the job remains Published."><TextInput type="date" value={form.application_deadline} onChange={(e) => set("application_deadline", e.target.value)}/></Field>
      <Field className="field-full" label="Description"><TextArea required maxLength={20000} value={form.description} onChange={(e) => set("description", e.target.value)} rows={5}/></Field>
      <Field label="Required skills" hint="One item per line; optional."><TextArea value={form.required_skills} onChange={(e) => set("required_skills", e.target.value)} rows={3}/></Field>
      <Field label="Qualifications" hint="One item per line; optional."><TextArea value={form.qualifications} onChange={(e) => set("qualifications", e.target.value)} rows={3}/></Field>
    </div>
    <div className="form-actions">{onCancel && <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>}<Button disabled={busy}>{busy && <span className="spinner"/>}{initial ? "Save changes" : "Create draft job"}</Button></div>
  </form>;
}
