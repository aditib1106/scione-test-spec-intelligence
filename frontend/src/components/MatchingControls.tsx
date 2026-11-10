import { useEffect, useRef, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  IconButton,
  MenuItem,
  Select,
  Snackbar,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import DownloadRoundedIcon from "@mui/icons-material/DownloadRounded";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import HistoryRoundedIcon from "@mui/icons-material/HistoryRounded";
import { request, useResource } from "../api";
import type { ProviderOption } from "../types";
import InferenceProgress, {
  recordInferenceDuration,
} from "./InferenceProgress";

interface MatchingAttempt {
  run_id: string;
  status: "completed" | "failed";
  provider: string;
  model: string;
  created_at: string;
  current_inputs: boolean;
}

interface MatchingOverview {
  source_run_id: string;
  report_input_fingerprint: string;
  input_fingerprint: string;
  current_run_id: string | null;
  runs: MatchingAttempt[];
}

export default function MatchingControls({
  documentId,
  comparisonId,
  aiApplied,
  providers,
  onComplete,
  compact = false,
}: {
  documentId: string;
  comparisonId: string;
  aiApplied: boolean;
  providers: ProviderOption[];
  onComplete: () => void;
  compact?: boolean;
}) {
  const path = `/api/specifications/${encodeURIComponent(documentId)}/matching`;
  const overview = useResource<MatchingOverview>(path);
  const supportedProviders = providers;
  const defaultProvider =
    supportedProviders.find((item) => item.available) || supportedProviders[0];
  const [providerId, setProviderId] = useState(defaultProvider?.id || "");
  const provider = supportedProviders.find((item) => item.id === providerId);
  const [model, setModel] = useState(defaultProvider?.model || "");
  const [outputLimit, setOutputLimit] = useState(
    defaultProvider?.output_limit ?? 8000,
  );
  const [permission, setPermission] = useState(false);
  const [open, setOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);
  const submitting = useRef(false);
  const requestController = useRef<AbortController | null>(null);
  const previousComparison = useRef(comparisonId);
  const activeDocument = useRef(documentId);
  activeDocument.current = documentId;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestController.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (comparisonId !== previousComparison.current) {
      previousComparison.current = comparisonId;
      overview.refresh();
      setPermission(false);
      if (!compact) setNotice("");
    }
  }, [comparisonId, compact, overview.refresh]);

  const start = () => {
    setPermission(false);
    setError("");
    setNotice("");
    overview.refresh();
    setOpen(true);
  };

  const run = async () => {
    const input = overview.data;
    if (
      submitting.current ||
      !input ||
      !permission ||
      !provider?.available ||
      !model.trim()
    )
      return;
    submitting.current = true;
    const submittedDocument = documentId;
    const controller = new AbortController();
    requestController.current = controller;
    setBusy(true);
    setError("");
    const inferenceStarted = performance.now();
    try {
      const result = await request<{
        run_id: string;
        status: string;
        cached: boolean;
      }>(path, {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          run_id: input.source_run_id,
          input_fingerprint: input.report_input_fingerprint,
          provider: provider.id,
          model: model.trim(),
          output_limit: outputLimit,
          permission: true,
          rerun: false,
        }),
      });
      if (
        !mounted.current ||
        controller.signal.aborted ||
        activeDocument.current !== submittedDocument
      )
        return;
      if (result.status !== "completed")
        throw new Error(
          "Matching did not complete. Check the saved attempt before trying again.",
        );
      if (!result.cached)
        recordInferenceDuration(
          "matching",
          (performance.now() - inferenceStarted) / 1000,
        );
      setOpen(false);
      setPermission(false);
      setNotice(
        result.cached
          ? "Reused the saved AI matching for these inputs."
          : "AI matching saved. The report is being refreshed.",
      );
      onComplete();
    } catch (reason) {
      if (
        !mounted.current ||
        controller.signal.aborted ||
        activeDocument.current !== submittedDocument
      )
        return;
      setError(
        reason instanceof Error
          ? reason.message
          : "Matching could not be completed. Please try again.",
      );
      setPermission(false);
    } finally {
      submitting.current = false;
      if (requestController.current === controller)
        requestController.current = null;
      if (mounted.current) {
        setBusy(false);
        overview.refresh();
      }
    }
  };

  const current = overview.data?.runs.find(
    (item) => item.run_id === overview.data?.current_run_id,
  );
  const olderSuccess = overview.data?.runs.find(
    (item) => item.status === "completed",
  );
  const failedLatest = overview.data?.runs[0]?.status === "failed";
  const validLimit =
    Number.isInteger(outputLimit) && outputLimit >= 512 && outputLimit <= 65536;

  const statusDescription = busy
    ? "Matching the saved requirements with your lab declarations…"
    : failedLatest
      ? "The latest AI matching attempt failed. The last saved comparison is still available."
      : current
        ? "Saved AI matching is available for the current information."
        : olderSuccess
          ? "The saved AI matching uses earlier information. Run matching for the current requirements and lab."
          : "Run AI matching to align requirement wording with your lab declarations.";
  const historyContent = (
    <>
      {overview.loading && !overview.data && (
        <Typography variant="caption">Loading matching history…</Typography>
      )}
      {overview.error && (
        <Alert
          severity="warning"
          action={<Button onClick={overview.refresh}>Retry</Button>}
        >
          {overview.error}
        </Alert>
      )}
      {overview.data?.runs.length === 0 && (
        <Typography variant="caption" color="text.secondary">
          No AI matching attempts have been saved for this specification.
        </Typography>
      )}
      <Stack spacing={1}>
        {overview.data?.runs.map((attempt) => (
          <Stack
            key={attempt.run_id}
            direction={{ xs: "column", sm: "row" }}
            justifyContent="space-between"
            spacing={1}
            sx={{
              p: 1.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Stack
                direction="row"
                spacing={0.75}
                alignItems="center"
                useFlexGap
                flexWrap="wrap"
              >
                <Chip
                  size="small"
                  label={
                    attempt.status === "completed" ? "Completed" : "Failed"
                  }
                  color={attempt.status === "completed" ? "success" : "error"}
                  variant="outlined"
                />
                <Typography variant="caption">
                  {attempt.run_id === overview.data?.current_run_id
                    ? "Current result"
                    : attempt.current_inputs
                      ? "Current inputs"
                      : "Earlier inputs"}
                </Typography>
              </Stack>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5, overflowWrap: "anywhere" }}
              >
                {attempt.provider} · {attempt.model} ·{" "}
                {new Date(attempt.created_at).toLocaleString()}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", overflowWrap: "anywhere" }}
              >
                Run {attempt.run_id}
              </Typography>
            </Box>
            <Button
              component="a"
              href={`${path}/${encodeURIComponent(attempt.run_id)}/artifacts`}
              download
              size="small"
              startIcon={<DownloadRoundedIcon />}
              sx={{ alignSelf: "flex-start", flexShrink: 0 }}
            >
              Download eval artifacts
            </Button>
          </Stack>
        ))}
      </Stack>
    </>
  );
  const matchingButton = (
    <Button
      variant={aiApplied ? "outlined" : "contained"}
      size="small"
      onClick={start}
      disabled={busy || !supportedProviders.some((item) => item.available)}
      startIcon={
        busy ? <CircularProgress size={15} /> : <AutoAwesomeOutlinedIcon />
      }
      sx={{
        flexShrink: 0,
        minHeight: 34,
        alignSelf: compact ? undefined : { xs: "flex-start", sm: "center" },
      }}
    >
      {busy ? "Matching…" : "Run AI matching"}
    </Button>
  );

  return (
    <Box
      sx={
        compact
          ? { minWidth: 0 }
          : { borderTop: "1px solid", borderColor: "divider", mt: 2.5, pt: 2 }
      }
    >
      {compact ? (
        <Stack
          direction="row"
          spacing={0.75}
          alignItems="center"
          useFlexGap
          flexWrap="wrap"
          sx={{ minHeight: 34 }}
        >
          <Tooltip title={statusDescription}>
            <Chip
              size="small"
              variant="outlined"
              color={
                failedLatest ? "warning" : aiApplied ? "success" : "default"
              }
              label={
                aiApplied ? "AI-assisted comparison" : "Rules-based comparison"
              }
              sx={{ height: 25, fontSize: 11, maxWidth: "100%" }}
            />
          </Tooltip>
          {matchingButton}
          <Tooltip title="Developer details · matching history">
            <IconButton
              aria-label="Developer details · matching history"
              size="small"
              color={failedLatest ? "warning" : "default"}
              onClick={() => {
                overview.refresh();
                setHistoryOpen(true);
              }}
            >
              <HistoryRoundedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      ) : (
        <>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ sm: "center" }}
            justifyContent="space-between"
            spacing={1.5}
          >
            <Box>
              <Typography variant="body2" fontWeight={600}>
                {aiApplied
                  ? "AI-assisted comparison"
                  : "Rules-based comparison"}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {busy
                  ? "Matching the saved requirements with your lab declarations…"
                  : current
                    ? "Saved AI matching is available for the current information."
                    : olderSuccess
                      ? "The saved AI matching uses earlier information. Run matching for the current requirements and lab."
                      : "Run AI matching to align requirement wording with your lab declarations."}
              </Typography>
            </Box>
            {matchingButton}
          </Stack>
          {!supportedProviders.some((item) => item.available) && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5 }}
            >
              A model API must be configured to run AI matching.
            </Typography>
          )}
          {notice && (
            <Alert
              severity="success"
              sx={{ mt: 1.5 }}
              onClose={() => setNotice("")}
            >
              {notice}
            </Alert>
          )}
          {failedLatest && !busy && (
            <Alert severity="warning" sx={{ mt: 1.5 }}>
              The latest AI matching attempt failed. The last saved comparison
              is still available. No automatic retry was made. Review the saved
              attempt before choosing to run matching again.
            </Alert>
          )}
          <Accordion
            disableGutters
            elevation={0}
            sx={{
              bgcolor: "transparent",
              mt: 0.5,
              "&:before": { display: "none" },
            }}
          >
            <AccordionSummary
              expandIcon={<ExpandMoreRoundedIcon />}
              sx={{
                minHeight: 36,
                px: 0,
                "& .MuiAccordionSummary-content": { my: 0.75 },
              }}
            >
              <Typography variant="caption" color="text.secondary">
                Developer details · matching history
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ px: 0, pt: 0 }}>
              {historyContent}
            </AccordionDetails>
          </Accordion>
        </>
      )}
      <Dialog
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Developer matching history</DialogTitle>
        <DialogContent dividers>
          {failedLatest && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              The latest AI matching attempt failed. The last saved comparison
              is still available. No automatic retry was made.
            </Alert>
          )}
          {historyContent}
        </DialogContent>
        <DialogActions sx={{ px: 3, py: 1.5 }}>
          <Button onClick={() => setHistoryOpen(false)}>Close history</Button>
        </DialogActions>
      </Dialog>
      <Snackbar
        open={compact && Boolean(notice)}
        autoHideDuration={6000}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        onClose={(_event, reason) => {
          if (reason !== "clickaway") setNotice("");
        }}
      >
        <Alert
          severity="success"
          variant="filled"
          onClose={() => setNotice("")}
          sx={{ width: "100%" }}
        >
          {notice}
        </Alert>
      </Snackbar>

      <Dialog
        open={open}
        onClose={() => {
          if (!busy) setOpen(false);
        }}
        maxWidth="sm"
      >
        <DialogTitle>Run AI matching</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Compare the saved requirements with your lab capabilities. Longer
            specifications can take a few minutes. Your saved report stays
            available while matching runs.
          </Typography>
          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}
          {overview.error && (
            <Alert
              severity="warning"
              sx={{ mb: 2 }}
              action={<Button onClick={overview.refresh}>Reload inputs</Button>}
            >
              {overview.error}
            </Alert>
          )}
          <FormControl fullWidth size="small" disabled={busy}>
            <InputLabel id="matching-provider-label">Model provider</InputLabel>
            <Select
              labelId="matching-provider-label"
              label="Model provider"
              value={providerId}
              onChange={(event) => {
                const next = supportedProviders.find(
                  (item) => item.id === event.target.value,
                );
                setProviderId(event.target.value);
                setModel(next?.model || "");
                setOutputLimit(next?.output_limit ?? 8000);
                setPermission(false);
              }}
            >
              {supportedProviders.map((item) => (
                <MenuItem
                  key={item.id}
                  value={item.id}
                  disabled={!item.available}
                >
                  {item.label}
                  {item.available ? "" : " · not configured"}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <Accordion
            disableGutters
            elevation={0}
            sx={{ mt: 1, "&:before": { display: "none" } }}
          >
            <AccordionSummary
              expandIcon={<ExpandMoreRoundedIcon />}
              sx={{ px: 0 }}
            >
              <Typography variant="body2">Model settings</Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ px: 0, pt: 0 }}>
              <Stack spacing={2}>
                <Typography variant="caption" color="text.secondary">
                  Complex specifications use up to eight planned model requests,
                  with no automatic retries. Leaving this page stops waiting; a
                  started operation may finish its planned requests and use
                  credits.
                </Typography>
                <TextField
                  label="Matching model"
                  fullWidth
                  value={model}
                  disabled={busy}
                  inputProps={{ maxLength: 200 }}
                  onChange={(event) => {
                    setModel(event.target.value);
                    setPermission(false);
                  }}
                />
                <TextField
                  label="Maximum output tokens"
                  type="number"
                  fullWidth
                  value={outputLimit}
                  disabled={busy}
                  onChange={(event) => {
                    setOutputLimit(Number(event.target.value));
                    setPermission(false);
                  }}
                  inputProps={{ min: 512, max: 65536, step: 512 }}
                  error={!validLimit}
                  helperText="Provider limits still apply. Saved results are reused when the inputs and model match."
                />
              </Stack>
            </AccordionDetails>
          </Accordion>
          <FormControlLabel
            sx={{
              alignItems: "flex-start",
              mx: 0,
              "& .MuiCheckbox-root": { pt: 0.5, pl: 0 },
            }}
            control={
              <Checkbox
                checked={permission}
                onChange={(event) => setPermission(event.target.checked)}
                disabled={busy}
              />
            }
            label={
              <Typography variant="body2" sx={{ pt: 0.5 }}>
                I allow sending the extracted specification and my lab
                declarations to {provider?.label || "the selected provider"}.
                API usage may incur charges.
              </Typography>
            }
          />
          {busy && (
            <Box sx={{ mt: 2 }}>
              <InferenceProgress
                title="Comparing your requirements"
                detail="Checking the saved lab information. The report updates when matching finishes."
              />
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}>
          <Button disabled={busy} onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={
              busy ||
              overview.loading ||
              Boolean(overview.error) ||
              !overview.data ||
              !permission ||
              !provider?.available ||
              !model.trim() ||
              !validLimit
            }
            onClick={run}
          >
            {busy ? "Matching…" : "Start matching"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
