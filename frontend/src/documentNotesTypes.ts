export interface DocumentNote {
  id: string;
  kind: "context" | "reference" | "document_info";
  title: string;
  detail: string;
  page?: number | null;
  quote?: string | null;
  reviewed: boolean;
}

export interface DocumentNotesReview {
  source_fingerprint: string;
  version: number;
  notes: DocumentNote[];
  pending_count: number;
  reviewed_count: number;
}
