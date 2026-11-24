import { reviewerLabel } from "./reviewerLabels";
import type {
  AssessmentReview,
  ComparisonData,
  ComparisonStatus,
  CustomerClarificationEvent,
} from "./comparisonTypes";

export type FrozenReviewComparison = ComparisonData & {
  machine_comparison_id?: string;
};

export interface ReviewSummaryRow {
  requirementId: string;
  testName: string;
  methods: string[];
  inclusion: "included" | "excluded";
  machineStatus: ComparisonStatus;
  sourceReviewState: string;
  reviewed: boolean;
  review: AssessmentReview | null;
  questionCount: number;
  replyCount: number;
  unansweredQuestionCount: number;
  clarificationLabel: string;
}

export interface ReviewSummaryData {
  rows: ReviewSummaryRow[];
  counts: {
    included: number;
    reviewed: number;
    notReviewed: number;
    excluded: number;
  };
}

function scopedEvents(
  report: FrozenReviewComparison,
  requirementId: string,
): CustomerClarificationEvent[] {
  if (!report.source_run_id) return [];
  const events = report.customer_clarifications?.[requirementId]?.events;
  if (!Array.isArray(events)) return [];
  const seen = new Set<string>();
  return events.filter((event) => {
    if (
      !event ||
      !event.event_id ||
      seen.has(event.event_id) ||
      event.document_id !== report.document.document_id ||
      event.source_run_id !== report.source_run_id ||
      event.requirement_id !== requirementId ||
      !["question", "customer_reply"].includes(event.event_kind)
    )
      return false;
    seen.add(event.event_id);
    return true;
  });
}

/** Summarize a frozen export without changing findings or interpreting a reply as approval. */
export function buildReviewSummary(
  report: FrozenReviewComparison,
  expectedMachineId: string,
): ReviewSummaryData {
  if (
    "_scione_comparison_export_reference" in report ||
    !Array.isArray(report.requirements) ||
    !expectedMachineId ||
    (report.machine_comparison_id !== undefined &&
      report.machine_comparison_id !== expectedMachineId)
  )
    throw new Error(
      "This review summary does not belong to the selected assessment.",
    );

  const rows = report.requirements.map((requirement): ReviewSummaryRow => {
    const excluded =
      requirement.comparison.status === "excluded" ||
      requirement.source_review_state === "excluded" ||
      Boolean(requirement.report_scope_exclusion);
    const saved = report.assessment_reviews?.[requirement.requirement_id];
    const valid =
      saved &&
      saved.comparison_id === expectedMachineId &&
      saved.requirement_id === requirement.requirement_id &&
      Boolean(report.report_input_fingerprint) &&
      saved.input_fingerprint === report.report_input_fingerprint &&
      saved.machine_status === requirement.comparison.status &&
      ["accepted", "pending"].includes(saved.review_status) &&
      ["keep", "clarify_customer", "outsource", "decline"].includes(
        saved.decision,
      );
    const review = valid ? saved : null;
    const events = scopedEvents(report, requirement.requirement_id);
    const questions = events.filter(
      (event) =>
        event.event_kind === "question" && event.question_id === event.event_id,
    );
    const questionIds = new Set(questions.map((event) => event.event_id));
    const replies = events.filter(
      (event) =>
        event.event_kind === "customer_reply" &&
        questionIds.has(event.question_id),
    );
    const answered = new Set(replies.map((event) => event.question_id));
    const unanswered = questions.filter(
      (question) => !answered.has(question.event_id),
    ).length;
    return {
      requirementId: requirement.requirement_id,
      testName: requirement.test_name,
      methods: [...requirement.methods],
      inclusion: excluded ? "excluded" : "included",
      machineStatus: requirement.comparison.status,
      sourceReviewState: requirement.source_review_state,
      reviewed: !excluded && review?.review_status === "accepted",
      review,
      questionCount: questions.length,
      replyCount: replies.length,
      unansweredQuestionCount: unanswered,
      clarificationLabel: unanswered
        ? `${unanswered} ${unanswered === 1 ? "question awaiting" : "questions awaiting"} recorded reply`
        : replies.length
          ? "Replies recorded"
          : "No records",
    };
  });
  const included = rows.filter((row) => row.inclusion === "included").length;
  const reviewed = rows.filter((row) => row.reviewed).length;
  return {
    rows,
    counts: {
      included,
      reviewed,
      notReviewed: included - reviewed,
      excluded: rows.length - included,
    },
  };
}

function csvCell(value: string | number | null | undefined): string {
  let text = String(value ?? "");
  const leading = text.match(/^[\s\u0000-\u001f\u007f]*/u)?.[0] ?? "";
  // Quoting alone does not stop spreadsheet formula execution. Preserve the
  // original value after an apostrophe when its leading content is dangerous.
  if (
    /[\u0000-\u001f\u007f]/u.test(leading) ||
    /^[\s\u0000-\u001f\u007f]*[=+\-@]/u.test(text)
  ) {
    text = `'${text}`;
  }
  return `"${text.replaceAll('"', '""')}"`;
}

/** Export every saved snapshot row, independent of the page's result filters. */
export function reviewSummaryCsv(
  report: FrozenReviewComparison,
  expectedMachineId: string,
): string {
  const summary = buildReviewSummary(report, expectedMachineId);
  const headers = [
    "Customer",
    "Specification",
    "Specification date",
    "Lab",
    "Source file",
    "Test",
    "Method",
    "Scope",
    "Comparison result",
    "Review status",
    "Follow-up",
    "Review note",
    "Recorded by",
    "Review time",
    "Questions",
    "Recorded replies",
    "Questions awaiting reply",
    "Reason for removal",
    "Saved report reference",
  ];
  const findings: Record<ComparisonStatus, string> = {
    fully_matched: "Matched",
    partially_matched: "Partial match",
    not_matched: "Gap",
    needs_information: "Needs information",
    excluded: "Excluded",
  };
  const decisions: Record<AssessmentReview["decision"], string> = {
    keep: "Keep assessment",
    clarify_customer: "Ask customer",
    outsource: "Plan to outsource",
    decline: "Do not offer",
  };
  const records = summary.rows.map((row) => {
    const requirement = report.requirements.find(
      (item) => item.requirement_id === row.requirementId,
    )!;
    return [
      report.document.company_name,
      report.document.specification_id,
      report.document.specification_date,
      report.lab_name,
      report.document.original_name,
      row.testName,
      row.methods.join("; "),
      row.inclusion === "included" ? "Included" : "Excluded",
      findings[row.machineStatus],
      row.inclusion === "excluded"
        ? "Excluded from scope"
        : row.reviewed
          ? "Reviewed"
          : "Not reviewed",
      row.review ? decisions[row.review.decision] : "",
      row.review?.note,
      row.review ? reviewerLabel(row.review) : "",
      row.review?.created_at,
      row.questionCount,
      row.replyCount,
      row.unansweredQuestionCount,
      requirement.report_scope_exclusion?.note,
      report.comparison_id,
    ];
  });
  return (
    "\uFEFF" +
    [headers, ...records]
      .map((cells) => cells.map(csvCell).join(","))
      .join("\r\n") +
    "\r\n"
  );
}
