export interface LibraryDocument {
  document_id: string;
  company_name: string | null;
  specification_id: string | null;
  title: string | null;
  revision: string | null;
  specification_date?: string | null;
  family_id: string;
  version: number;
  original_name: string;
  created_at: string;
  updated_at: string;
  requirement_count: number;
  review_count: number;
  run_id: string | null;
  page_count: number;
  provider?: string | null;
  document_review_issues?: string[];
}

export interface LibraryClaim {
  parameter_id: string | null;
  kind?: string;
  label: string;
  display_value: string;
  unit: string | null;
  stage: string | null;
  sequence?: number | null;
  configuration?: string | null;
  source_page: number | null;
  raw_text: string;
  status: string;
}

export interface LibraryRequirement {
  requirement_id: string;
  test_name: string;
  methods: string[];
  claims: LibraryClaim[];
}

export interface LibraryDetail {
  document: LibraryDocument;
  versions: LibraryDocument[];
  requirements: LibraryRequirement[];
}

export interface LibraryMetadata {
  company_name: string;
  specification_id: string;
  title: string;
  revision: string;
  specification_date?: string | null;
  family_id: string | null;
}
