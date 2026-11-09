import type { LibraryDocument } from "./libraryTypes";

export type ComparisonStatus =
  | "fully_matched"
  | "partially_matched"
  | "not_matched"
  | "needs_information"
  | "excluded";

export interface ComparisonCheck {
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
  requirement_claim?: {
    normalization_state: string;
    sequence?: number | null;
    configuration?: string | null;
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
  comparison_id: string;
  matching_run_id?: string | null;
  comparison_engine?: "ai_semantic_matching" | "deterministic_rules";
  source_run_id?: string;
  report_input_fingerprint?: string;
  report_workspace_version?: number;
  created_at: string;
  document: LibraryDocument;
  lab_name: string;
  lab_version: number;
  requirements: ComparisonRequirement[];
  summary: Partial<Record<ComparisonStatus, number>>;
  pdf_page_count?: number;
}
