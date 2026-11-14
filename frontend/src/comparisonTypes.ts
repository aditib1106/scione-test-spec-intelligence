import type { LibraryDocument } from "./libraryTypes";
import type { DocumentNotesReview } from "./documentNotesTypes";

export type ComparisonStatus =
  | "fully_matched"
  | "partially_matched"
  | "not_matched"
  | "needs_information"
  | "excluded";

export type ReasonSource = "system" | "ai" | "ai_assisted" | "unrecorded";

export interface ComparisonCheck {
  id?: string;
  label: string;
  parameter_id: string | null;
  kind: string;
  stage: string | null;
  unit: string | null;
  requested: string;
  declared: string | null;
  status: "supported" | "gap" | "unknown" | "informational";
  reason: string;
  source_pages: number[];
  comparison_basis?:
    | "semantic_alignment"
    | "deterministic_exact_fields"
    | "deterministic_literal_text"
    | "literal_descriptive_correspondence"
    | "source_optional_route_policy"
    | null;
  comparison_metadata?: {
    reason?: string;
    semantic_inference?: boolean;
    numeric_evaluation?: boolean;
  }[];
  candidate_lab_entries?: {
    claim_id: string;
    label: string;
    value: string;
    stage: string;
    context: string | null;
    confirmed: false;
  }[];
  requirement_claim?: {
    normalization_state: string;
    raw_text?: string;
    sequence?: number | null;
    configuration?: string | null;
    source_context?: string | null;
  } | null;
}

export interface ComparisonRequirement {
  requirement_id: string;
  test_name: string;
  methods: string[];
  capability_id: string | null;
  source_review_state: string;
  report_scope_exclusion?: {
    note: string;
    occurred_at: string;
    actor?: string;
  } | null;
  comparison: {
    status: ComparisonStatus;
    checks: ComparisonCheck[];
    supported_count?: number;
    gap_count?: number;
    unknown_count?: number;
  };
}

export interface ComparisonData {
  document_notes_review?: DocumentNotesReview;
  explanation_sources?: Record<string, Record<string, ReasonSource>>;
  export_snapshot_id?: string;
  comparison_id: string;
  matching_run_id?: string | null;
  comparison_engine?: "ai_semantic_matching" | "deterministic_rules";
  source_run_id?: string;
  source_coverage_issues?: string[];
  report_input_fingerprint?: string;
  report_workspace_version?: number;
  created_at: string;
  document: LibraryDocument;
  lab_name: string;
  lab_version: number;
  requirements: ComparisonRequirement[];
  summary: Partial<Record<ComparisonStatus, number>>;
  pdf_page_count?: number;
  assessment_reviews?: Record<string, AssessmentReview>;
}

export interface AssessmentReview {
  review_id: string;
  comparison_id: string;
  requirement_id: string;
  version: number;
  review_status: "accepted" | "pending";
  decision: "keep" | "clarify_customer" | "outsource" | "decline";
  note: string;
  actor: string;
  created_at: string;
  machine_status: ComparisonStatus;
  input_fingerprint: string;
}
