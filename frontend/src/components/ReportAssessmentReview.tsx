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
        sx={{
          px: 2,
          py: 1.5,
          borderTop: "1px solid #e0e7f0",
          bgcolor: "#f8faff",
        }}
      >
        <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
          {accepted ? (
            <Chip
              icon={<CheckRoundedIcon />}
              size="small"
              color="success"
              label="Assessment reviewed"
            />
          ) : (
            <Button
              size="small"
              variant="contained"
              startIcon={<CheckRoundedIcon />}
              disabled={busy}
              onClick={() => void save(true)}
            >
              {busy ? "Saving…" : "Accept assessment"}
            </Button>
          )}
          <Button
            size="small"
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
          {current && current.decision !== "keep" && (
            <Typography variant="body2" fontWeight={600}>
              {decisions[current.decision]}
            </Typography>
          )}
        </Stack>
        <Typography
          variant="caption"
          color="text.secondary"
          display="block"
          sx={{ mt: 0.75 }}
        >
          Accept records your review. Gaps and missing information stay visible.
        </Typography>
        {current?.note && (
          <Typography
            variant="body2"
            sx={{ mt: 0.75, overflowWrap: "anywhere" }}
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
        onClose={() => {
          if (!busy) setOpen(false);
        }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Review decision</DialogTitle>
        <DialogContent>
          <Typography fontWeight={650} sx={{ mb: 2 }}>
            {requirement.test_name}
          </Typography>
          <Stack gap={2}>
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
              inputProps={{ maxLength: 4000 }}
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
            <Button
              component="a"
              href={`/api/comparisons/${encodeURIComponent(documentId)}/reviews?download=true`}
              size="small"
              sx={{ alignSelf: "flex-start" }}
            >
              Download review history
            </Button>
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
