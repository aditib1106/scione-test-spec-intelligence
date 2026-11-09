import { useEffect, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import RemoveCircleOutlineRoundedIcon from "@mui/icons-material/RemoveCircleOutlineRounded";
import UndoRoundedIcon from "@mui/icons-material/UndoRounded";
import { request } from "../api";
import type { ComparisonData, ComparisonRequirement } from "../comparisonTypes";

export default function ReportScopeControls({
  documentId,
  report,
  requirement,
  onSaved,
}: {
  documentId: string;
  report: ComparisonData;
  requirement: ComparisonRequirement;
  onSaved: (report: ComparisonData) => void;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const submitting = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  if (
    !report.source_run_id ||
    !report.report_input_fingerprint ||
    report.report_workspace_version === undefined
  )
    return null;
  const excluded = Boolean(requirement.report_scope_exclusion);

  const save = async () => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const updated = await request<ComparisonData>(
        `/api/reports/${encodeURIComponent(documentId)}/exclusions`,
        {
          method: "POST",
          body: JSON.stringify({
            run_id: report.source_run_id,
            input_fingerprint: report.report_input_fingerprint,
            version: report.report_workspace_version,
            requirement_ids: [requirement.requirement_id],
            excluded: !excluded,
            note: note.trim(),
          }),
        },
      );
      if (!mounted.current) return;
      onSaved(updated);
      setOpen(false);
      setNotice(
        excluded
          ? "Test restored to this report."
          : "Test removed from this report. You can restore it here.",
      );
    } catch (reason) {
      if (mounted.current)
        setError(
          reason instanceof Error
            ? reason.message
            : "The report scope could not be saved.",
        );
    } finally {
      if (mounted.current) {
        submitting.current = false;
        setBusy(false);
      }
    }
  };

  return (
    <Box
      sx={{
        p: { xs: 2, md: 2.5 },
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      {requirement.source_review_state === "excluded" ? (
        <Alert
          severity="info"
          action={
            <Button
              component={RouterLink}
              to={`/specifications?${new URLSearchParams({ document: documentId, requirement: requirement.requirement_id })}`}
            >
              Review source exclusion
            </Button>
          }
        >
          This test was excluded during specification review. Restore it there
          to include it in a report.
        </Alert>
      ) : (
        <>
          {notice && (
            <Alert
              severity="success"
              sx={{ mb: 1.5 }}
              onClose={() => setNotice("")}
            >
              {notice}
            </Alert>
          )}
          {excluded && (
            <Alert severity="info" sx={{ mb: 1.5 }}>
              Removed from this report's scope. The extracted requirement and
              matching results are preserved.
              {requirement.report_scope_exclusion?.note && (
                <Typography variant="caption" component="p" sx={{ mt: 0.5 }}>
                  Note: {requirement.report_scope_exclusion.note}
                </Typography>
              )}
            </Alert>
          )}
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ sm: "center" }}
            spacing={1.5}
          >
            <Button
              variant="outlined"
              size="small"
              color={excluded ? "primary" : "inherit"}
              startIcon={
                excluded ? (
                  <UndoRoundedIcon />
                ) : (
                  <RemoveCircleOutlineRoundedIcon />
                )
              }
              onClick={() => {
                setNote("");
                setError("");
                setOpen(true);
              }}
              sx={{ alignSelf: "flex-start" }}
            >
              {excluded ? "Restore to this report" : "Remove from this report"}
            </Button>
            <Typography variant="caption" color="text.secondary">
              Changes this report's scope only.
            </Typography>
          </Stack>
        </>
      )}
      <Dialog
        open={open}
        onClose={() => {
          if (!busy) setOpen(false);
        }}
        maxWidth="sm"
      >
        <DialogTitle>
          {excluded
            ? "Restore this test?"
            : "Remove this test from the report?"}
        </DialogTitle>
        <DialogContent>
          <Typography variant="subtitle1" sx={{ mb: 1 }}>
            {requirement.test_name}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {excluded
              ? "This test will return to the report with its current comparison result."
              : "The test will be excluded from this report's active scope and totals. Its extracted requirements and original matching result remain saved. You can restore it later."}
          </Typography>
          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}
          <TextField
            fullWidth
            multiline
            minRows={2}
            label="Scope note (optional)"
            value={note}
            disabled={busy}
            onChange={(event) => setNote(event.target.value)}
            inputProps={{ maxLength: 4000 }}
          />
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}>
          <Button disabled={busy} onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={busy}
            onClick={save}
            startIcon={busy ? <CircularProgress size={15} /> : undefined}
          >
            {busy ? "Saving…" : excluded ? "Restore test" : "Remove test"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
