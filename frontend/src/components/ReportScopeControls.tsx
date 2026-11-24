import { useEffect, useId, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import RemoveCircleOutlineRoundedIcon from "@mui/icons-material/RemoveCircleOutlineRounded";
import UndoRoundedIcon from "@mui/icons-material/UndoRounded";
import MoreHorizRoundedIcon from "@mui/icons-material/MoreHorizRounded";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
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
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const menuId = useId();
  const dialogTitleId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const submitting = useRef<symbol | null>(null);
  const mounted = useRef(true);
  const context = JSON.stringify([
    documentId,
    report.source_run_id,
    report.report_input_fingerprint,
    report.report_workspace_version,
    requirement.requirement_id,
    requirement.source_review_state,
    Boolean(requirement.report_scope_exclusion),
  ]);
  const currentContext = useRef(context);
  currentContext.current = context;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    setOpen(false);
    setAnchor(null);
    setNote("");
    setError("");
    setBusy(false);
    submitting.current = null;
  }, [context]);

  const restoreFocus = () =>
    requestAnimationFrame(() => {
      const target = trigger.current?.isConnected
        ? trigger.current
        : document.querySelector<HTMLElement>("[data-report-focus]");
      target?.focus({ preventScroll: true });
    });
  const close = () => {
    setOpen(false);
    restoreFocus();
  };

  if (
    !report.source_run_id ||
    !report.report_input_fingerprint ||
    report.report_workspace_version === undefined
  )
    return null;
  const excluded = Boolean(requirement.report_scope_exclusion);

  const save = async () => {
    if (submitting.current || requirement.source_review_state === "excluded")
      return;
    const submission = Symbol();
    const savedContext = context;
    submitting.current = submission;
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
      if (!mounted.current || currentContext.current !== savedContext) return;
      setOpen(false);
      onSaved(updated);
      restoreFocus();
    } catch (reason) {
      if (mounted.current && currentContext.current === savedContext)
        setError(
          reason instanceof Error
            ? reason.message
            : "The report scope could not be saved.",
        );
    } finally {
      if (mounted.current && submitting.current === submission) {
        submitting.current = null;
        setBusy(false);
      }
    }
  };

  return (
    <>
      <Tooltip title="More test actions">
        <IconButton
          ref={trigger}
          aria-label="More test actions"
          aria-haspopup="menu"
          aria-controls={anchor ? menuId : undefined}
          aria-expanded={Boolean(anchor)}
          disabled={busy}
          size="small"
          onClick={(event) => setAnchor(event.currentTarget)}
          sx={{ flexShrink: 0 }}
        >
          <MoreHorizRoundedIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Menu
        id={menuId}
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        {requirement.source_review_state === "excluded" ? (
          <MenuItem
            component={RouterLink}
            to={`/specifications?${new URLSearchParams({ document: documentId, requirement: requirement.requirement_id })}`}
            onClick={() => setAnchor(null)}
          >
            <ListItemIcon>
              <DescriptionOutlinedIcon fontSize="small" />
            </ListItemIcon>
            Review source exclusion
          </MenuItem>
        ) : (
          <MenuItem
            disabled={busy}
            onClick={() => {
              setAnchor(null);
              setNote("");
              setError("");
              setOpen(true);
            }}
            sx={{ color: excluded ? "primary.main" : "error.main" }}
          >
            <ListItemIcon sx={{ color: "inherit" }}>
              {excluded ? (
                <UndoRoundedIcon fontSize="small" />
              ) : (
                <RemoveCircleOutlineRoundedIcon fontSize="small" />
              )}
            </ListItemIcon>
            {excluded ? "Restore to this report" : "Remove from this report"}
          </MenuItem>
        )}
      </Menu>
      <Dialog
        open={open}
        aria-labelledby={dialogTitleId}
        disableRestoreFocus
        disableEscapeKeyDown={busy}
        onClose={(_, reason) => {
          if (!busy && reason !== "backdropClick") close();
        }}
        maxWidth="sm"
      >
        <DialogTitle id={dialogTitleId}>
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
          {excluded && requirement.report_scope_exclusion?.note && (
            <Alert severity="info" sx={{ mb: 2 }}>
              <Typography variant="body2" fontWeight={650}>
                Reason for removal
              </Typography>
              <Typography
                variant="body2"
                sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
              >
                {requirement.report_scope_exclusion.note}
              </Typography>
            </Alert>
          )}
          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}
          <TextField
            fullWidth
            multiline
            minRows={2}
            label="Note (optional)"
            value={note}
            disabled={busy}
            onChange={(event) => setNote(event.target.value)}
            inputProps={{ maxLength: 4000 }}
          />
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}>
          <Button disabled={busy} onClick={close}>
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
    </>
  );
}
