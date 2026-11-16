export type EmployeeRole = "admin" | "hr_manager" | "recruiter" | "interviewer";
export type EmployeeStatus = "active" | "invited" | "disabled";
export type JobStatus = "draft" | "published" | "paused" | "closed" | "archived";
export type ApplicationStage =
  | "applied" | "screening" | "shortlisted" | "interview" | "selected"
  | "offer" | "hired" | "rejected" | "withdrawn";
export type InterviewStatus = "scheduled" | "completed" | "cancelled";
export type OfferStatus =
  | "draft" | "pending_approval" | "approved" | "sent"
  | "accepted" | "rejected" | "withdrawn" | "expired";

export interface Employee {
  id: string;
  full_name: string | null;
  email: string;
  role: EmployeeRole;
  status: EmployeeStatus;
  created_at?: string;
  updated_at?: string;
  last_login_at?: string | null;
}

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface Job {
  id: string;
  title: string;
  department: string;
  employment_type: string;
  location: string;
  work_mode: string;
  min_experience: number | null;
  max_experience: number | null;
  salary_min: number | null;
  salary_max: number | null;
  description: string;
  required_skills: string[];
  qualifications: string[];
  vacancies: number;
  application_deadline: string | null;
  status: JobStatus;
  created_at?: string;
  updated_at?: string;
}

export interface Candidate {
  id: string;
  full_name: string;
  email: string;
  phone: string | null;
  location: string | null;
  linkedin_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface Application {
  id: string;
  candidate_id: string;
  job_id: string;
  source: "public_portal" | "recruiter" | "referral";
  stage: ApplicationStage;
  created_by_id: string | null;
  created_at: string;
  updated_at: string;
  candidate: Candidate;
}

export interface ApplicationNote {
  id: string;
  application_id: string;
  author_employee_id: string;
  author_name: string | null;
  body: string;
  created_at: string;
}

export interface StoredDocument {
  id: string;
  application_id: string;
  media_type: string;
  byte_size: number;
  checksum_sha256: string;
  created_at: string;
}

export interface InterviewFeedback {
  id: string;
  interviewer_employee_id: string;
  interviewer_name: string | null;
  rating: number;
  strengths: string;
  areas_for_improvement: string;
  recommendation: "strong_hire" | "hire" | "no_hire" | "strong_no_hire";
  created_at: string;
  updated_at: string;
}

export interface InterviewAssignment {
  id: string;
  interviewer_employee_id: string;
  interviewer_name: string | null;
  assigned_at: string;
  removed_by_id: string | null;
  removed_at: string | null;
}

export interface InterviewRound {
  id: string;
  application_id?: string | null;
  candidate_full_name: string;
  job_title: string;
  starts_at: string;
  ends_at: string;
  timezone_name: string;
  status: InterviewStatus;
  created_at: string;
  updated_at: string;
  interviewers: InterviewAssignment[];
  feedback: InterviewFeedback[];
}

export interface OfferRevision {
  id: string;
  revision_number: number;
  compensation_amount: number;
  compensation_currency: string;
  compensation_basis: string;
  expires_at: string;
  letter_size: number;
  created_by_employee_id: string;
  created_at: string;
}

export interface Offer {
  id: string;
  application_id: string;
  status: OfferStatus;
  current_revision_number: number;
  approved_revision_number: number | null;
  approved_by_employee_id: string | null;
  approved_at: string | null;
  sent_at: string | null;
  responded_at: string | null;
  withdrawn_at: string | null;
  expired_at: string | null;
  created_by_employee_id: string;
  created_at: string;
  updated_at: string;
  current_revision: OfferRevision;
}

export interface Notification {
  id: string;
  event_type: string;
  title: string;
  body: string;
  resource_type: string;
  resource_id: string | null;
  offer_id: string | null;
  created_at: string;
  read_at: string | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
}
