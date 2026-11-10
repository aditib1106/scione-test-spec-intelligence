import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Snackbar,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import CodeRoundedIcon from "@mui/icons-material/CodeRounded";
import { ApiError, request } from "../api";
import type { ProviderOption } from "../types";
import { claimReportGenerationIntent } from "../reportGeneration";
import InferenceProgress from "./InferenceProgress";
import MatchingControls from "./MatchingControls";

interface MatchingInputs {
  source_run_id: string;
  report_input_fingerprint: string;
  current_run_id: string | null;
}

function generationError(reason: unknown) {
  if (reason instanceof ApiError && reason.status === 409)
    return "The specification or lab information changed, or a comparison is already running. Refresh the report before trying again.";
  const message =
    reason instanceof Error
      ? reason.message
      : "The comparison could not finish.";
  if (
    /input guard|character limit|whole-test matching calls|batch.*limit|source was truncated/i.test(
      message,
    )
  )
    return "These requirements exceed the current comparison limit. Nothing was omitted. Review the oversized test or split the specification before generating again.";
  if (/429|503|unavailable|high demand|rate.limit/i.test(message))
    return "The analysis service is busy. Your saved report is still available. Try again in a few minutes.";
  if (
    /schema|invalid.output|JSON|validation error|structured output/i.test(
      message,
    )
  )
    return "The comparison could not be verified. Your saved report is still available. Try again or review the extracted requirements.";
  return message;
}

export default function ReportGenerationControls({
  documentId,
  comparisonId,
  aiApplied,
  providers,
  generationIntentId,
  onComplete,
}: {
  documentId: string;
  comparisonId: string;
  aiApplied: boolean;
  providers: ProviderOption[];
  generationIntentId?: string | null;
  onComplete: () => void;
}) {
  const configured = providers.find((provider) => provider.available);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<"preparing" | "comparing">("preparing");
  const [progressOpen, setProgressOpen] = useState(false);
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const mounted = useRef(false);
  const submitting = useRef(false);
  const activeDocument = useRef(documentId);
  activeDocument.current = documentId;
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controllerRef.current?.abort();
    };
  }, []);

  async function generateReport() {
    if (submitting.current) return;
    submitting.current = true;
    const submittedDocument = documentId;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError("");
    setNotice("");
    setPhase("preparing");
    setProgressOpen(true);
    const isCurrent = () =>
      mounted.current &&
      !controller.signal.aborted &&
      activeDocument.current === submittedDocument;
    const path = `/api/specifications/${encodeURIComponent(documentId)}/matching`;
    try {
      const input = await request<MatchingInputs>(path, {
        signal: controller.signal,
      });
      if (!isCurrent()) return;
      // A current completed result is already a valid report input. Merely
      // revisiting or refreshing it must not start another paid model operation.
      if (input.current_run_id) {
        setProgressOpen(false);
        setNotice("Your report is up to date.");
        onComplete();
        return;
      }
      if (!configured)
        throw new Error(
          "Report generation is not configured. Your saved information is available; contact your workspace administrator.",
        );
      setPhase("comparing");
      const result = await request<{ status: string; cached: boolean }>(path, {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          run_id: input.source_run_id,
          input_fingerprint: input.report_input_fingerprint,
          provider: configured.id,
          model: configured.model,
          output_limit: configured.output_limit ?? 8000,
          permission: true,
          rerun: false,
        }),
      });
      if (!isCurrent()) return;
      if (result.status !== "completed")
        throw new Error(
          "The comparison did not finish. Your saved report is still available.",
        );
      setProgressOpen(false);
      setNotice(
        result.cached ? "Your report is up to date." : "Report generated.",
      );
      onComplete();
    } catch (reason) {
      if (isCurrent()) setError(generationError(reason));
    } finally {
      submitting.current = false;
      if (controllerRef.current === controller) controllerRef.current = null;
      if (mounted.current) setBusy(false);
    }
  }

  const generateRef = useRef(generateReport);
  generateRef.current = generateReport;
  useEffect(() => {
    if (!generationIntentId) return;
    // Deferring one event-loop turn lets StrictMode's probe cleanup cancel the
    // first schedule. The nonce is consumed only when the real action starts.
    const timer = window.setTimeout(() => {
      if (claimReportGenerationIntent(generationIntentId))
        void generateRef.current();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [generationIntentId]);

  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Tooltip
        title={
          aiApplied
            ? "This report uses the current specification and lab comparison."
            : "Generate a report to compare these requirements with your saved lab information using AI."
        }
      >
        <Chip
          label={aiApplied ? "Compared" : "Preview"}
          size="small"
          color={aiApplied ? "success" : "default"}
          variant="outlined"
          sx={{ height: 25, fontSize: 12 }}
        />
      </Tooltip>
      <Tooltip title="Uses AI to compare the saved specification and lab information. Current results are reused.">
        <span>
          <Button
            variant={aiApplied ? "outlined" : "contained"}
            size="small"
            onClick={() => {
              if (busy) setProgressOpen(true);
              else void generateReport();
            }}
            startIcon={
              busy ? (
                <CircularProgress size={15} />
              ) : (
                <AutoAwesomeOutlinedIcon />
              )
            }
            sx={{ whiteSpace: "nowrap", minHeight: 34 }}
          >
            {busy
              ? "Generating…"
              : aiApplied
                ? "Update report"
                : "Generate report"}
          </Button>
        </span>
      </Tooltip>
      <Tooltip title="Developer report tools">
        <span>
          <IconButton
            aria-label="Developer report tools"
            size="small"
            disabled={busy}
            onClick={() => setDeveloperOpen(true)}
          >
            <CodeRoundedIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>

      <Dialog
        open={progressOpen}
        onClose={() => setProgressOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {error ? "Report needs another look" : "Generating your report"}
        </DialogTitle>
        <DialogContent>
          {busy ? (
            <InferenceProgress
              title={
                phase === "preparing"
                  ? "Preparing your comparison"
                  : "Comparing your requirements"
              }
              detail={
                phase === "preparing"
                  ? "Reading the latest saved specification and lab information."
                  : "Comparing test methods, conditions and reporting. Longer specifications may take a few minutes."
              }
            />
          ) : (
            error && <Alert severity="warning">{error}</Alert>
          )}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            Your saved information stays available. The report updates when the
            comparison finishes.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setProgressOpen(false)}>
            {busy ? "View saved report" : "Close"}
          </Button>
          {error && (
            <Button
              variant="contained"
              disabled={busy}
              onClick={() => void generateReport()}
            >
              Try again
            </Button>
          )}
        </DialogActions>
      </Dialog>

      <Dialog
        open={developerOpen}
        onClose={() => setDeveloperOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Developer report tools</DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Model settings, matching history and original evaluation artifacts.
          </Typography>
          {developerOpen && (
            <MatchingControls
              compact
              documentId={documentId}
              comparisonId={comparisonId}
              aiApplied={aiApplied}
              providers={providers}
              onComplete={onComplete}
            />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeveloperOpen(false)}>
            Close developer tools
          </Button>
        </DialogActions>
      </Dialog>
      <Snackbar
        open={Boolean(notice)}
        autoHideDuration={5000}
        onClose={() => setNotice("")}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          severity="success"
          variant="filled"
          onClose={() => setNotice("")}
        >
          {notice}
        </Alert>
      </Snackbar>
    </Stack>
  );
}
