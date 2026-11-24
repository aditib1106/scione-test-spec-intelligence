import { useEffect, useId, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import FileDownloadOutlinedIcon from "@mui/icons-material/FileDownloadOutlined";
import { request } from "../api";
import {
  buildReviewSummary,
  reviewSummaryCsv,
  type FrozenReviewComparison,
} from "../reviewSummary";
import { specificationVersionLabel } from "../specificationDates";
import type { ComparisonStatus } from "../comparisonTypes";

export interface ReviewSummarySelection {
  documentId: string;
  comparisonId: string;
  exportSnapshotId: string;
  sourceRunId?: string;
  inputFingerprint?: string;
}

interface Props {
  selection: ReviewSummarySelection;
  latestExportSnapshotId?: string;
  onClose: () => void;
}

const findingLabels: Record<ComparisonStatus, string> = {
  fully_matched: "Matched",
  partially_matched: "Partial match",
  not_matched: "Gap",
  needs_information: "Needs information",
  excluded: "Excluded",
};
const decisions = {
  keep: "Keep assessment",
  clarify_customer: "Ask customer",
  outsource: "Plan to outsource",
  decline: "Do not offer",
};

function validSnapshot(
  value: FrozenReviewComparison,
  selection: ReviewSummarySelection,
) {
  return Boolean(
    value &&
    typeof value === "object" &&
    !("_scione_comparison_export_reference" in value) &&
    value.comparison_id === selection.exportSnapshotId &&
    value.machine_comparison_id === selection.comparisonId &&
    value.document?.document_id === selection.documentId &&
    (selection.sourceRunId === undefined ||
      value.source_run_id === selection.sourceRunId) &&
    (selection.inputFingerprint === undefined ||
      value.report_input_fingerprint === selection.inputFingerprint) &&
    Array.isArray(value.requirements) &&
    value.requirements.every(
      (row) =>
        row &&
        typeof row.requirement_id === "string" &&
        typeof row.test_name === "string" &&
        Array.isArray(row.methods) &&
        row.methods.every((method) => typeof method === "string") &&
        row.comparison &&
        Object.hasOwn(findingLabels, row.comparison.status),
    ),
  );
}

function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Read one exact export snapshot; a newer review never silently replaces it. */
export default function ReportReviewSummary({
  selection,
  latestExportSnapshotId,
  onClose,
}: Props) {
  const id = useId();
  const context = JSON.stringify([
    selection.documentId,
    selection.comparisonId,
    selection.exportSnapshotId,
    selection.sourceRunId,
    selection.inputFingerprint,
  ]);
  const [loaded, setLoaded] = useState<{
    context: string;
    report: FrozenReviewComparison;
  } | null>(null);
  const [failure, setFailure] = useState<{
    context: string;
    message: string;
  } | null>(null);
  const [retry, setRetry] = useState(0);
  const downloads = useRef(new Map<string, number>());
  const report = loaded?.context === context ? loaded.report : null;
  const error = failure?.context === context ? failure.message : "";
  const summary = report
    ? buildReviewSummary(report, selection.comparisonId)
    : null;

  useEffect(() => {
    const controller = new AbortController();
    setLoaded(null);
    setFailure(null);
    void request<FrozenReviewComparison>(
      `/api/comparison-snapshots/${encodeURIComponent(selection.exportSnapshotId)}/json`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (controller.signal.aborted) return;
        if (!validSnapshot(value, selection))
          throw new Error(
            "This saved summary does not belong to the selected assessment. Close it and reopen the current report.",
          );
        setLoaded({ context, report: value });
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setFailure({
            context,
            message:
              reason instanceof Error
                ? reason.message
                : "The saved review summary could not be loaded.",
          });
      });
    return () => controller.abort();
  }, [context, retry]);

  useEffect(
    () => () => {
      for (const [url, timer] of downloads.current) {
        window.clearTimeout(timer);
        URL.revokeObjectURL(url);
      }
      downloads.current.clear();
    },
    [],
  );

  function download() {
    if (!report) return;
    const csv = reviewSummaryCsv(report, selection.comparisonId);
    const url = URL.createObjectURL(
      new Blob([csv], { type: "text/csv;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `scione-review-summary-${selection.exportSnapshotId.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64) || "saved"}.csv`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    downloads.current.set(
      url,
      window.setTimeout(() => {
        URL.revokeObjectURL(url);
        downloads.current.delete(url);
      }, 1000),
    );
  }

  return (
    <Dialog
      open
      onClose={onClose}
      fullWidth
      maxWidth="lg"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-description`}
      PaperProps={{ sx: { maxHeight: "90dvh" } }}
    >
      <DialogTitle id={`${id}-heading`} sx={{ pr: 7 }}>
        <Box component="span" id={`${id}-title`}>
          Review summary
        </Box>
        <IconButton
          aria-label="Close review summary"
          onClick={onClose}
          sx={{ position: "absolute", right: 16, top: 16 }}
        >
          <CloseRoundedIcon />
        </IconButton>
        {report && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mt: 0.75, overflowWrap: "anywhere" }}
          >
            {report.document.company_name || "Customer not assigned"} ·{" "}
            {report.document.specification_id || report.document.original_name}{" "}
            · {specificationVersionLabel(report.document)}
          </Typography>
        )}
      </DialogTitle>
      <DialogContent dividers sx={{ p: { xs: 1.5, sm: 2.5 } }}>
        <Typography
          id={`${id}-description`}
          variant="body2"
          color="text.secondary"
          sx={{ mb: 2 }}
        >
          Saved review summary. This is not a final approval.
        </Typography>
        {latestExportSnapshotId &&
          latestExportSnapshotId !== selection.exportSnapshotId && (
            <Alert severity="info" sx={{ mb: 2 }}>
              Newer saved review available. Close and reopen this summary to
              view it.
            </Alert>
          )}
        {error ? (
          <Alert
            severity="info"
            action={
              <Button
                color="inherit"
                onClick={() => setRetry((value) => value + 1)}
              >
                Retry
              </Button>
            }
          >
            {error}
          </Alert>
        ) : !summary ? (
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="center"
            gap={1.5}
            role="status"
            sx={{ py: 6 }}
          >
            <CircularProgress size={22} />
            <Typography variant="body2">
              Loading saved review summary…
            </Typography>
          </Stack>
        ) : (
          <>
            <Stack direction="row" gap={1} flexWrap="wrap" sx={{ mb: 2 }}>
              <Chip
                label={`Included ${summary.counts.included}`}
                variant="outlined"
              />
              <Chip
                label={`Reviewed ${summary.counts.reviewed}`}
                color="success"
                variant="outlined"
              />
              <Chip
                label={`Not reviewed ${summary.counts.notReviewed}`}
                variant="outlined"
              />
              <Chip
                label={`Excluded ${summary.counts.excluded}`}
                variant="outlined"
              />
            </Stack>
            <TableContainer
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1.5,
              }}
            >
              <Table
                size="small"
                aria-label="Saved review summary"
                sx={{
                  minWidth: 800,
                  "& td": { verticalAlign: "top", py: 1.5 },
                  "& th": { bgcolor: "#f5f7fa", fontWeight: 700 },
                }}
              >
                <TableHead>
                  <TableRow>
                    <TableCell>Test / method</TableCell>
                    <TableCell>Comparison finding</TableCell>
                    <TableCell>Your review</TableCell>
                    <TableCell>Follow-up</TableCell>
                    <TableCell>Customer clarifications</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {summary.rows.map((row) => (
                    <TableRow
                      key={row.requirementId}
                      sx={{
                        bgcolor:
                          row.inclusion === "excluded" ? "#fafbfc" : undefined,
                      }}
                    >
                      <TableCell
                        sx={{
                          minWidth: 180,
                          maxWidth: 280,
                          overflowWrap: "anywhere",
                        }}
                      >
                        <Typography variant="body2" fontWeight={650}>
                          {row.testName}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {row.methods.join(" · ") || "Method not specified"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          label={findingLabels[row.machineStatus]}
                          color={
                            row.machineStatus === "fully_matched"
                              ? "success"
                              : row.machineStatus === "not_matched"
                                ? "error"
                                : row.machineStatus === "partially_matched"
                                  ? "warning"
                                  : "default"
                          }
                          variant="outlined"
                        />
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">
                          {row.inclusion === "excluded"
                            ? "Excluded from scope"
                            : row.reviewed
                              ? "Reviewed"
                              : "Not reviewed"}
                        </Typography>
                        {row.review && (
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            component="time"
                            dateTime={row.review.created_at}
                          >
                            {dateLabel(row.review.created_at)}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell sx={{ minWidth: 150, maxWidth: 260 }}>
                        <Typography
                          variant="body2"
                          fontWeight={row.review ? 600 : 400}
                        >
                          {row.review ? decisions[row.review.decision] : "—"}
                        </Typography>
                        {row.review?.note && (
                          <Typography
                            variant="body2"
                            title={row.review.note}
                            sx={{
                              mt: 0.5,
                              whiteSpace: "pre-wrap",
                              overflowWrap: "anywhere",
                              display: "-webkit-box",
                              WebkitLineClamp: 3,
                              WebkitBoxOrient: "vertical",
                              overflow: "hidden",
                            }}
                          >
                            {row.review.note}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell sx={{ minWidth: 175 }}>
                        <Typography
                          variant="body2"
                          color={
                            row.unansweredQuestionCount
                              ? "warning.dark"
                              : "text.secondary"
                          }
                        >
                          {row.clarificationLabel}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            {!summary.rows.length && (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ py: 3, textAlign: "center" }}
              >
                No test items in this saved assessment.
              </Typography>
            )}
          </>
        )}
      </DialogContent>
      <DialogActions
        sx={{
          px: { xs: 1.5, sm: 2.5 },
          py: 1.5,
          justifyContent: "space-between",
          gap: 1,
        }}
      >
        <Button
          startIcon={<FileDownloadOutlinedIcon />}
          disabled={!report}
          onClick={download}
        >
          Download summary CSV
        </Button>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
