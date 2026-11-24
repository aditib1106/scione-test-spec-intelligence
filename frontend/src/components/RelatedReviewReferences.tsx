import { useEffect, useId, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import { request } from "../api";
import { useAuth } from "../auth";
import { specificationVersionLabel } from "../specificationDates";
import { reviewerLabel } from "../reviewerLabels";
import type {
  AssessmentReview,
  ComparisonCheck,
  ComparisonStatus,
} from "../comparisonTypes";

interface Reference {
  review: Omit<AssessmentReview, "input_fingerprint">;
  document: {
    document_id: string;
    title: string | null;
    company_name: string | null;
    specification_id: string | null;
    specification_date: string | null;
    original_name: string;
    revision: string | null;
  };
  test_name: string;
  methods: string[];
  lab_version: number;
  lab_changed: boolean;
  same_source: false;
  different_customer: boolean;
  customer_context: "same" | "different" | "unassigned";
  comparison: { status: ComparisonStatus; checks: ComparisonCheck[] };
}
interface Response {
  document_id: string;
  comparison_id: string;
  requirement_id: string;
  entries: Reference[];
  reference_only: true;
  history_limited?: boolean;
}
interface Props {
  documentId: string;
  comparisonId: string;
  requirementId: string;
  inputFingerprint?: string;
}
const decisions = {
  keep: "Keep assessment",
  clarify_customer: "Ask customer",
  outsource: "Plan to outsource",
  decline: "Do not offer",
};
const findings: Record<ComparisonStatus, string> = {
  fully_matched: "Matched",
  partially_matched: "Partial match",
  not_matched: "Gap",
  needs_information: "Needs information",
  excluded: "Excluded",
};
const checks = {
  supported: "Match",
  gap: "Gap",
  unknown: "Needs information",
  informational: "For reference",
};
function validResponse(value: Response, props: Props): boolean {
  return Boolean(
    value &&
    value.document_id === props.documentId &&
    value.comparison_id === props.comparisonId &&
    value.requirement_id === props.requirementId &&
    value.reference_only === true &&
    Array.isArray(value.entries) &&
    value.entries.length <= 10 &&
    value.entries.every(
      (entry) =>
        entry &&
        entry.same_source === false &&
        entry.document &&
        typeof entry.document.document_id === "string" &&
        entry.document.document_id !== props.documentId &&
        typeof entry.test_name === "string" &&
        Array.isArray(entry.methods) &&
        entry.methods.every((method) => typeof method === "string") &&
        ["same", "different", "unassigned"].includes(entry.customer_context) &&
        typeof entry.lab_changed === "boolean" &&
        entry.review &&
        typeof entry.review.review_id === "string" &&
        typeof entry.review.note === "string" &&
        typeof entry.review.created_at === "string" &&
        ["accepted", "pending"].includes(entry.review.review_status) &&
        Object.hasOwn(decisions, entry.review.decision) &&
        entry.comparison &&
        Object.hasOwn(findings, entry.comparison.status) &&
        Array.isArray(entry.comparison.checks) &&
        entry.comparison.checks.every(
          (check) =>
            check &&
            typeof check.label === "string" &&
            typeof check.requested === "string" &&
            (check.declared === null || typeof check.declared === "string") &&
            typeof check.reason === "string" &&
            Object.hasOwn(checks, check.status),
        ),
    ),
  );
}
function dateLabel(value: string | null | undefined) {
  if (!value) return "Date not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}

/** Read-only context from other documents, fetched only when its tab is opened. */
export default function RelatedReviewReferences(props: Props) {
  const { session } = useAuth();
  const { documentId, comparisonId, requirementId, inputFingerprint } = props;
  const panelId = useId();
  const [response, setResponse] = useState<Response | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setResponse(null);
    setError("");
    if (!inputFingerprint) {
      setError("Refresh this report before looking up past reviews.");
      return () => controller.abort();
    }
    const query = new URLSearchParams({
      requirement_id: requirementId,
      comparison_id: comparisonId,
      input_fingerprint: inputFingerprint,
    });
    void request<Response>(
      `/api/comparisons/${encodeURIComponent(documentId)}/related-reviews?${query}`,
      {
        signal: controller.signal,
      },
    )
      .then((result) => {
        if (controller.signal.aborted) return;
        if (
          !validResponse(result, {
            documentId,
            comparisonId,
            requirementId,
            inputFingerprint,
          })
        )
          throw new Error(
            "The assessment changed. Refresh before viewing past reviews.",
          );
        setResponse(result);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Past reviews could not be loaded.",
          );
      });
    return () => controller.abort();
  }, [documentId, comparisonId, requirementId, inputFingerprint, attempt]);

  if (error)
    return (
      <Alert
        severity="info"
        action={
          <Button
            color="inherit"
            size="small"
            onClick={() => setAttempt((v) => v + 1)}
          >
            Retry
          </Button>
        }
      >
        {error}
      </Alert>
    );
  if (!response)
    return (
      <Stack
        role="status"
        direction="row"
        gap={1}
        alignItems="center"
        justifyContent="center"
        sx={{ py: 5 }}
      >
        <CircularProgress size={16} />
        <Typography variant="body2">Looking for past reviews…</Typography>
      </Stack>
    );
  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        Same test name and recorded method references, in this lab’s other
        specifications. Past decisions are references; they do not change this
        assessment.
      </Typography>
      {response.history_limited && (
        <Typography variant="caption" color="text.secondary">
          Some older review details are too large to include in this lookup.
        </Typography>
      )}
      {!response.entries.length ? (
        <Box sx={{ py: 3, textAlign: "center" }}>
          <Typography fontWeight={650} variant="body2">
            No related past reviews found
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            Recent review history is checked. A different test name or method
            edition is kept separate.
          </Typography>
        </Box>
      ) : (
        response.entries.map((entry, index) => (
          <Box
            key={entry.review.review_id}
            sx={{
              border: "1px solid #dfe6ef",
              borderRadius: 2,
              overflow: "hidden",
            }}
          >
            <Box sx={{ p: 2 }}>
              <Stack
                direction="row"
                gap={1}
                justifyContent="space-between"
                alignItems="flex-start"
              >
                <Typography
                  variant="body2"
                  fontWeight={700}
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {entry.document.company_name || "Customer not assigned"}
                  {" · "}
                  {entry.document.specification_id ||
                    entry.document.title ||
                    entry.document.original_name}
                </Typography>
                <Chip
                  size="small"
                  label={
                    entry.review.review_status === "accepted"
                      ? "Reviewed"
                      : "Needs review"
                  }
                  sx={{ flexShrink: 0 }}
                />
              </Stack>
              <Typography variant="caption" color="text.secondary">
                {specificationVersionLabel(entry.document)} ·{" "}
                {entry.methods.join(" · ")}
              </Typography>
              <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mt: 1 }}>
                {entry.customer_context !== "same" && (
                  <Chip
                    size="small"
                    variant="outlined"
                    label={
                      entry.customer_context === "different"
                        ? "Different customer"
                        : "Customer not established"
                    }
                  />
                )}
                {entry.lab_changed && (
                  <Chip
                    size="small"
                    variant="outlined"
                    label="Lab information has changed"
                  />
                )}
              </Stack>
              <Typography variant="body2" fontWeight={650} sx={{ mt: 1.5 }}>
                {decisions[entry.review.decision]}
              </Typography>
              {entry.review.note && (
                <Typography
                  variant="body2"
                  sx={{
                    mt: 0.5,
                    whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere",
                  }}
                >
                  {entry.review.note}
                </Typography>
              )}
              <Typography
                variant="caption"
                color="text.secondary"
                display="block"
                sx={{ mt: 1 }}
              >
                <Box component="span" title={entry.review.actor}>
                  {reviewerLabel(entry.review, session?.user)}
                </Box>
                {" · "}Recorded {dateLabel(entry.review.created_at)} · Previous
                finding: {findings[entry.comparison.status]}
              </Typography>
            </Box>
            <Accordion
              disableGutters
              elevation={0}
              sx={{
                borderTop: "1px solid #e8edf3",
                "&:before": { display: "none" },
              }}
            >
              <AccordionSummary
                expandIcon={<ExpandMoreRoundedIcon />}
                id={`${panelId}-${index}`}
                aria-controls={`${panelId}-${index}-details`}
                sx={{ minHeight: 44 }}
              >
                <Typography
                  variant="body2"
                  color="primary.main"
                  fontWeight={600}
                >
                  View the previous comparison
                </Typography>
              </AccordionSummary>
              <AccordionDetails sx={{ p: 0 }}>
                <TableContainer>
                  <Table
                    size="small"
                    aria-label="Previous requirement comparison"
                    sx={{ minWidth: 540 }}
                  >
                    <TableHead>
                      <TableRow>
                        <TableCell>Requirement</TableCell>
                        <TableCell>Previous customer request</TableCell>
                        <TableCell>Lab information then</TableCell>
                        <TableCell>Finding then</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {entry.comparison.checks.map((check, i) => (
                        <TableRow key={check.id || `${i}`}>
                          <TableCell
                            sx={{ width: "22%", verticalAlign: "top" }}
                          >
                            {check.label}
                            {check.stage && (
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                display="block"
                              >
                                {check.stage}
                              </Typography>
                            )}
                          </TableCell>
                          <TableCell
                            sx={{
                              width: "27%",
                              verticalAlign: "top",
                              overflowWrap: "anywhere",
                            }}
                          >
                            {check.requested}
                          </TableCell>
                          <TableCell
                            sx={{
                              width: "27%",
                              verticalAlign: "top",
                              overflowWrap: "anywhere",
                            }}
                          >
                            {check.declared || "Not recorded"}
                          </TableCell>
                          <TableCell sx={{ verticalAlign: "top" }}>
                            {checks[check.status]}
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              display="block"
                            >
                              {check.reason}
                            </Typography>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </AccordionDetails>
            </Accordion>
          </Box>
        ))
      )}
    </Stack>
  );
}
