import type { DocumentNotesReview } from "./documentNotesTypes";

export interface SpecificationUploadReceipt {
  document_id: string;
  created?: boolean;
  reused?: boolean;
  restored?: boolean;
  has_saved_analysis?: boolean;
  original_name?: string;
}

export interface Alias {
  text: string;
  language: string;
}
export interface CatalogTest {
  id: string;
  name: string;
  description: string;
  aliases: Alias[];
  method_ids: string[];
}
export interface CatalogMethod {
  id: string;
  identifier: string;
  name: string;
  edition: string | null;
  aliases: Alias[];
}
export interface Catalog {
  id: string;
  name: string;
  version: number;
  synthetic: boolean;
  source_note: string;
  tests: CatalogTest[];
  methods: CatalogMethod[];
}
export interface OperatingLimit {
  parameter: string;
  minimum: string | null;
  maximum: string | null;
  values: string[];
  unit: string | null;
  supported_values: string[];
}
export interface AtomicClaim {
  id: string;
  parameter_id: string | null;
  label: string;
  kind: "condition" | "criterion" | "reporting" | "scope" | "instruction";
  stage: string;
  operator:
    "=" | "range" | "<=" | ">=" | "<" | ">" | "one_of" | "text" | "required";
  value: string | null;
  minimum: string | null;
  maximum: string | null;
  tolerance: string | null;
  values: string[];
  unit: string | null;
  text_value: string | null;
  availability: "supported" | "unsupported" | "unknown";
  raw_text: string;
  source_pages: number[];
  source_context?: string | null;
  normalization_state: "parsed" | "unresolved" | "reviewed";
  configuration: string | null;
  sequence: number | null;
}
export interface LabRecords {
  storage: string;
  backend: string;
  workspace_id: string;
  lab_version: number;
  capability_count: number;
  claim_count: number;
  claims: (AtomicClaim & {
    capability_id: string;
    normalizer_version: string;
  })[];
  revisions: unknown[];
}
export interface Capability {
  id: string;
  canonical_test_id: string;
  method_ids: string[];
  declared_constraints: string | null;
  structured_limits: OperatingLimit[];
  atomic_claims?: AtomicClaim[];
  notes: string | null;
  price: string | null;
  currency: string | null;
  pricing_basis: string | null;
}
export interface CustomLabTest {
  id?: string;
  name: string;
  description?: string;
  aliases?: string[];
}
export interface CustomLabMethod {
  id?: string;
  name: string;
  identifier?: string;
  aliases?: string[];
}
export interface CapabilitySubmission {
  capability: Capability;
  custom_test?: CustomLabTest;
  custom_methods?: CustomLabMethod[];
}
export interface CapabilitySaveResponse {
  lab: LabProfile;
  catalog: Catalog;
}
export interface LabProfile {
  id: string;
  name: string;
  description: string;
  version: number;
  capabilities: Capability[];
  custom_tests?: CatalogTest[];
  custom_methods?: CatalogMethod[];
}
export interface DocumentSummary {
  document_id: string;
  original_name: string;
  created_at: string;
  page_count: number;
  extraction_status: string;
  latest_run_id: string | null;
  requirement_count: number;
  review_version: number;
  confirmed_count: number;
  needs_review_count: number;
}
export interface ProviderOption {
  id: string;
  label: string;
  model: string;
  available: boolean;
  output_limit: number;
}
export interface Workspace {
  lab: LabProfile;
  catalog: Catalog;
  documents: DocumentSummary[];
  providers: ProviderOption[];
}
export interface PageProps {
  workspace: Workspace;
  onRefresh: () => void;
}
export interface RequirementValue {
  name: string;
  raw_value: string;
  unit?: string | null;
  context?: string | null;
}
export interface Requirement {
  id: string;
  original_test_name: string;
  requested_methods: {
    code: string;
    revision?: string | null;
    role?: string | null;
  }[];
  conditions: RequirementValue[];
  acceptance_criteria: RequirementValue[];
  applicability: string[];
  special_instructions: string[];
  atomic_claims?: AtomicClaim[];
  evidence: { page: number; quote: string; section?: string | null }[];
}
export interface ExtractionRun {
  run_id: string;
  started_at: string;
  provider: string;
  model: string;
  prompt_version: string;
  strategy: string;
  latency_ms: number;
  raw_response: string;
  usage: {
    input_tokens: number | null;
    output_tokens: number | null;
    total_tokens: number | null;
  };
  extraction: {
    document: { title?: string | null };
    requirements: Requirement[];
    review_issues: string[];
  };
}
export type ReviewState =
  "ready" | "confirmed" | "needs_attention" | "excluded";
export interface ReviewSet {
  run_id: string;
  version: number;
  decisions: Record<
    string,
    { requirement_id: string; status: string; note: string; updated_at: string }
  >;
}
export interface SpecificationData {
  document_notes_review?: DocumentNotesReview;
  document_id: string;
  original_name: string;
  document: {
    page_count: number;
    extraction_status: string;
    warnings: string[];
    pages: { page_number: number; text: string }[];
  };
  run: ExtractionRun | null;
  review: ReviewSet | null;
  facts_version?: number;
  facts_revision?: {
    revision_id: string;
    version: number;
    changed_requirement_ids: string[];
    note: string;
  } | null;
  assessments: Record<
    string,
    { state: ReviewState; reasons: string[]; notices?: string[] }
  >;
  normalized_requirements?: Record<
    string,
    {
      display_test_name?: string;
      claims: (AtomicClaim & {
        display_value?: string;
        source_page?: number | null;
        source_atom_ids?: string[];
      })[];
    }
  >;
  coverage_issues: string[];
  document_review_issues?: string[];
  failure: {
    attempt_id?: string | null;
    message: string;
    occurred_at: string;
    previous_run_id: string | null;
  } | null;
}
export interface MappingChoice {
  canonical_test_id: string | null;
  selected_method_codes: string[] | null;
  capability_id: string | null;
  capability_verdict: "unreviewed" | "supported" | "unsupported";
  note: string;
}
export interface GapLine {
  requirement: Requirement;
  canonical_test_id: string | null;
  canonical_test_name: string | null;
  candidate_test_ids: string[];
  match_basis: string;
  considered_method_codes: string[];
  capability: Capability | null;
  lab_method_identifiers: string[];
  status: string;
  reasons: string[];
  extraction_review_state: ReviewState;
  extraction_review_reasons: string[];
  mapping_choice: MappingChoice | null;
  operation_assessment: string;
  operation_checks: string[];
  operation_reasons: string[];
  quote: {
    quantity: string;
    unit_price: string;
    currency: string;
    pricing_basis: string;
    subtotal: string;
  } | null;
}
export interface ReportSnapshot {
  report_id: string;
  document_id: string;
  original_name: string;
  input_fingerprint: string;
  source_run: ExtractionRun;
  lab: LabProfile;
  catalog: Catalog;
  lines: GapLine[];
  totals_by_currency: Record<string, string>;
  warnings: string[];
  workspace: {
    version: number;
    mappings: Record<string, MappingChoice>;
    quantities: Record<string, { quantity: string; note: string }>;
  };
}
export interface LineState {
  needs_review: boolean;
  support_basis: string | null;
  scope_state: string;
  quote_eligible: boolean;
  batch_eligible: boolean;
}
export interface ReportData {
  report: ReportSnapshot;
  line_states: Record<string, LineState>;
  saved_reports: { report_id: string; created_at: string; current: boolean }[];
  coverage_issues: string[];
}
