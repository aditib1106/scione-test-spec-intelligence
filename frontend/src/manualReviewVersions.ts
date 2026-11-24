import type { ComparisonData } from "./comparisonTypes";

export function manualAssessmentKey(report: ComparisonData): string {
  return JSON.stringify([
    report.document.document_id,
    report.comparison_id,
    report.source_run_id ?? null,
    report.report_input_fingerprint ?? null,
  ]);
}

export function sameManualAssessment(
  current: ComparisonData,
  incoming: ComparisonData,
): boolean {
  return Boolean(
    incoming?.document &&
    incoming.comparison_id &&
    manualAssessmentKey(current) === manualAssessmentKey(incoming),
  );
}

const version = (value: unknown) =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : 0;

/** A full export may replace another only if none of its known records go backwards. */
export function manualReviewIsBehind(
  current: ComparisonData,
  incoming: ComparisonData,
): boolean {
  for (const [id, review] of Object.entries(current.assessment_reviews ?? {})) {
    if (
      version(review.version) >
      version(incoming.assessment_reviews?.[id]?.version)
    )
      return true;
  }
  for (const [id, thread] of Object.entries(
    current.customer_clarifications ?? {},
  )) {
    if (
      version(thread.version) >
      version(incoming.customer_clarifications?.[id]?.version)
    )
      return true;
  }
  const notes = current.document_notes_review;
  if (notes) {
    const next = incoming.document_notes_review;
    if (
      !next ||
      notes.source_fingerprint !== next.source_fingerprint ||
      version(notes.version) > version(next.version)
    )
      return true;
  }
  return false;
}
