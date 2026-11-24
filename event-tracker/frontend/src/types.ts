// API responses are defined by backend/app/domain.py. Keep these projections aligned.
export type Topic = {
  tags?: string[];
  topic_id: string;
  title: string;
  description: string;
  status: "draft" | "active";
  notification_preferences: "standard" | "digest_only";
  created_at: string;
  updated_at: string;
};
export type Source = {
  source_id: string;
  name: string;
  type: "fixture" | "replay" | "rss" | "manual" | "web_search" | "webpage";
  endpoint: string;
  config: Record<string, unknown>;
  reason?: string;
  fact_evidence?: string;
  role?: "anchor" | "branch" | "channel";
  health_status?: string;
  retirement_recommended?: boolean;
  ai_verdict?: "approve" | "optional" | "skip" | null;
  registered?: boolean;
};
export type Fact = { fact: string; evidence_ids: string[] };
export type TimelineItem = {
  occurred_at: string;
  description: string;
  evidence_ids: string[];
};
export type EventState = {
  current_status: string;
  timeline: TimelineItem[];
  key_facts: Fact[];
  sources: string[];
};
export type Event = {
  event_id: string;
  topic_id: string;
  title: string;
  state: EventState;
  version: number;
  created_at: string;
  updated_at: string;
};
export type Evidence = {
  evidence_id: string;
  source_id: string;
  external_id: string;
  url: string;
  title: string;
  content: string;
  published_at: string;
  fetched_at: string;
  content_hash: string;
  canonical_evidence_id: string;
  relation?: string;
};
export type Assessment = {
  evidence_id: string;
  is_relevant: boolean;
  association: {
    type: "existing_event" | "new_event" | "unresolved";
    event_id: string | null;
    title: string | null;
    reason: string;
  };
  novelty: string;
  supported_claims: Fact[];
  conflicts: string[];
  proposed_state: EventState | null;
  requires_review: boolean;
  reason_codes: string[];
  update_kind: string | null;
  expected_version: number | null;
};
export type EvidenceAssessment = {
  evidence_id: string;
  is_relevant: boolean;
  event_decisions: Omit<Assessment, "evidence_id" | "is_relevant">[];
};
export type Review = {
  evidence: Evidence;
  assessment: Assessment;
  event_decisions?: EvidenceAssessment["event_decisions"];
};
export type ReviewResolution = {
  resolution_id: string;
  evidence_id: string;
  decision: "dismiss" | "accept";
  note: string;
  actor: string;
  resolved_at: string;
  original_assessment: Assessment | EvidenceAssessment;
  event_id?: string;
  run_id?: string;
  superseded_facts?: Fact[];
};
export type Run = {
  run_id: string;
  topic_id: string;
  status:
    | "pending"
    | "running"
    | "completed"
    | "completed_with_errors"
    | "failed"
    | "cancelled";
  provider: "mock" | "qwen" | "human";
  trigger_type: "manual" | "resume" | "human_review";
  fixture_batch_id: string;
  model?: string;
  started_at: string | null;
  completed_at: string | null;
  counts: Record<string, number>;
  error: unknown;
  snapshot: { options?: { as_of?: string }; resume_of?: string; sources?: Source[] };
};
export type Step = {
  step_id: string;
  sequence: number;
  step_name: string;
  evidence_id: string | null;
  status: string;
  attempt: number;
  input_refs: string[];
  output_refs: string[];
  decision: unknown;
  error: unknown;
  started_at: string;
  completed_at: string | null;
};
export type RunEvidence = {
  source_id: string;
  external_id: string;
  evidence_id: string;
  title: string;
  status: string;
  attempts: number;
  error: { code: string; message: string } | null;
};
export type RunDetail = { run: Run; steps: Step[]; evidence: RunEvidence[] };
export type Notification = {
  notification_id: string;
  event_id: string;
  transition_id: string;
  action: "immediate" | "digest" | "record_only";
  priority: string;
  reason_codes: string[];
  created_at: string;
};
export type Transition = {
  transition_id: string;
  run_id?: string;
  before_state: EventState | null;
  after_state: EventState;
  evidence_ids: string[];
  committed_at: string;
  impact_reason?: string;
};
export type EventDetail = {
  event: Event;
  evidence: Evidence[];
  history: Transition[];
  reviews: Review[];
  updates?: Array<Notification & { provider: Run["provider"] }>;
};
export type Dashboard = {
  topic: Topic;
  approved_sources: Source[];
  events: Event[];
  recent_runs: Run[];
  reviews: Review[];
  review_resolutions: ReviewResolution[];
  notification_counts: Record<Notification["action"], number>;
};
export type EvidenceDecision = {
  evidence: Evidence;
  status: string;
  assessment: Assessment | EvidenceAssessment | null;
  error: unknown;
};
export type ModelStatus = {
  configured: boolean;
  base_url: string;
  model: string;
  key_configured: boolean;
  missing: string[];
};
export type SearchStatus = {
  provider: "auto" | "bocha" | "tavily" | "free";
  active_provider: "bocha" | "tavily" | "free";
  active_label: string;
  bocha_configured: boolean;
  tavily_configured: boolean;
  jina_configured: boolean;
  web_fetch_jina_fallback: boolean;
};
export type Corpus = {
  corpus_id: string;
  name: string;
  corpus_path: string;
  records: number;
  cutoffs: string[];
  default_goal?: string;
  anchor_terms?: string[];
  provenance: string;
};
