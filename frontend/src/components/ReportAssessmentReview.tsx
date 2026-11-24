import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import ReportReviewHistory from "./ReportReviewHistory";
import CustomerClarifications from "./CustomerClarifications";
import { ApiError, request } from "../api";
import type {
  AssessmentReview,
  ComparisonData,
  ComparisonRequirement,
} from "../comparisonTypes";

const decisions: Record<AssessmentReview["decision"], string> = {
  keep: "Keep the current assessment",
  clarify_customer: "Ask the customer to clarify",
  outsource: "Plan to outsource this test",
  decline: "Do not offer this test",
};

export default function ReportAssessmentReview({
  documentId,
  report,
  requirement,
  onSaved,
}: {
  documentId: string;
  report: ComparisonData;
  requirement: ComparisonRequirement;
  onSaved: (updated: ComparisonData) => void;
}) {
  const current = report.assessment_reviews?.[requirement.requirement_id];
  const accepted = current?.review_status === "accepted";
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [decision, setDecision] = useState<AssessmentReview["decision"]>(
    current?.decision ?? "keep",
  );
  const [reviewStatus, setReviewStatus] = useState<
    AssessmentReview["review_status"]
  >(current?.review_status ?? "accepted");
  const [note, setNote] = useState(current?.note ?? "");
  const mounted = useRef(true);
  const submitting = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  if (
    !report.source_run_id ||
    !report.report_input_fingerprint ||
    requirement.comparison.status === "excluded"
  )
    return null;
  async function save(quick = false) {
    if (submitting.current) return;
    if (!quick && decision !== "keep" && !note.trim()) {
      setError("Add a short note about this follow-up decision.");
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const updated = await request<ComparisonData>(
        `/api/comparisons/${encodeURIComponent(documentId)}/reviews`,
        {
          method: "POST",
          body: JSON.stringify({
            comparison_id: report.comparison_id,
            input_fingerprint: report.report_input_fingerprint,
            requirement_id: requirement.requirement_id,
            expected_version: current?.version ?? 0,
            review_status: quick ? "accepted" : reviewStatus,
            decision: quick ? (current?.decision ?? "keep") : decision,
            note: quick ? (current?.note ?? "") : note.trim(),
          }),
        },
      );
      if (!mounted.current) return;
      setOpen(false);
      onSaved(updated);
    } catch (reason) {
      if (mounted.current)
        setError(
          reason instanceof ApiError && reason.status === 409
            ? "This assessment or review changed. Refresh the report before saving again."
            : reason instanceof Error
              ? reason.message
              : "The review could not be saved.",
        );
    } finally {
      if (mounted.current) {
        submitting.current = false;
        setBusy(false);
      }
    }
  }
  return (
    <>
      <Box
        component="section"
        aria-label="Your assessment review"
        sx={{
          px: { xs: 2, sm: 2.5 },
          py: 1.25,
          borderTop: "1px solid #e0e7f0",
          bgcolor: "#fafbfd",
        }}
      >
        <Stack
          direction="row"
          gap={1.5}
          alignItems="center"
          justifyContent="space-between"
          flexWrap="wrap"
        >
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
            <Typography variant="subtitle2" fontWeight={700} sx={{ mr: 0.5 }}>
              Your review
            </Typography>
            {accepted ? (
              <Chip
                icon={<CheckRoundedIcon />}
                size="small"
                variant="outlined"
                label="Assessment reviewed"
                sx={{
                  bgcolor: "#f1f5fa",
                  color: "#52677f",
                  borderColor: "#dce5ef",
                  "& .MuiChip-icon": { color: "#4166a3" },
                }}
              />
            ) : (
              <Button
                size="medium"
                variant="contained"
                startIcon={<CheckRoundedIcon />}
                disabled={busy}
                onClick={() => void save(true)}
              >
                {busy ? "Saving…" : "Accept assessment"}
              </Button>
            )}
            <Button
              size="medium"
              variant="outlined"
              startIcon={<EditOutlinedIcon />}
              disabled={busy}
              onClick={() => {
                setDecision(current?.decision ?? "keep");
                setReviewStatus(current?.review_status ?? "accepted");
                setNote(current?.note ?? "");
                setError("");
                setOpen(true);
              }}
            >
              Edit decision
            </Button>
          </Stack>
          <Stack
            direction="row"
            gap={0.5}
            flexWrap="wrap"
            role="group"
            aria-label="Review records"
            sx={{ pl: { sm: 1.5 }, borderLeft: { sm: "1px solid #d7e1ef" } }}
          >
            <ReportReviewHistory
              documentId={documentId}
              report={report}
              requirementId={requirement.requirement_id}
            />
            <CustomerClarifications
              documentId={documentId}
              report={report}
              requirementId={requirement.requirement_id}
              onSaved={onSaved}
            />
          </Stack>
        </Stack>
        {(!accepted || (current && current.decision !== "keep")) && (
          <Typography
            variant="caption"
            color="text.secondary"
            display="block"
            sx={{ mt: 0.75 }}
          >
            {current && current.decision !== "keep"
              ? decisions[current.decision]
              : "Accept records your review; comparison findings stay unchanged."}
          </Typography>
        )}
        {current?.note && (
          <Typography
            variant="body2"
            title={current.note}
            sx={{
              mt: 0.5,
              overflowWrap: "anywhere",
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {current.note}
          </Typography>
        )}
        {error && !open && (
          <Alert severity="error" sx={{ mt: 1 }}>
            {error}
          </Alert>
        )}
      </Box>
      <Dialog
        open={open}
        onClose={(_, reason) => {
          if (!busy && reason !== "backdropClick") setOpen(false);
        }}
        disableEscapeKeyDown={busy}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Review decision</DialogTitle>
        <DialogContent>
          <Typography fontWeight={650} sx={{ mb: 2 }}>
            {requirement.test_name}
          </Typography>
          <Stack
            component="fieldset"
            disabled={busy}
            inert={busy}
            gap={2}
            sx={{ m: 0, p: 0, border: 0, minWidth: 0 }}
          >
            {error && <Alert severity="error">{error}</Alert>}
            <TextField
              select
              label="Follow-up decision"
              value={decision}
              onChange={(event) =>
                setDecision(event.target.value as AssessmentReview["decision"])
              }
              fullWidth
            >
              {Object.entries(decisions).map(([value, label]) => (
                <MenuItem key={value} value={value}>
                  {label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              multiline
              minRows={3}
              maxRows={6}
              label={
                decision === "keep"
                  ? "Review note (optional)"
                  : "Follow-up note"
              }
              value={note}
              onChange={(event) => setNote(event.target.value)}
              fullWidth
              required={decision !== "keep"}
              inputProps={{
                maxLength: 4000,
                "aria-label":
                  decision === "keep"
                    ? "Review note (optional)"
                    : "Follow-up note",
              }}
              helperText={
                decision === "clarify_customer"
                  ? "Records follow-up needed; no customer message is sent."
                  : decision === "outsource"
                    ? "Records a plan. Partner availability and price still need confirmation."
                    : undefined
              }
            />
            <TextField
              select
              label="Review status"
              value={reviewStatus}
              onChange={(event) =>
                setReviewStatus(
                  event.target.value as AssessmentReview["review_status"],
                )
              }
              fullWidth
            >
              <MenuItem value="accepted">Reviewed</MenuItem>
              <MenuItem value="pending">Needs review</MenuItem>
            </TextField>
            <Typography variant="caption" color="text.secondary">
              The comparison findings stay unchanged. Use the source or lab
              links in this report to correct the underlying information.
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={busy} onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={busy}
            onClick={() => void save()}
          >
            {busy ? "Saving…" : "Save review"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
