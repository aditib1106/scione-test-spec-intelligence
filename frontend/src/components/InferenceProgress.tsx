import { useEffect, useMemo, useState } from "react";
import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import type { InferenceStepProgress } from "../api";

type EstimateKey = "analysis" | "matching";
interface DurationSample {
  seconds: number;
  units: number;
}
const storageKey = (key: EstimateKey) => `scione:completed-duration:${key}:v1`;

const stageMessages: Record<InferenceStepProgress["stage"], string> = {
  planning: "Planning the analysis steps.",
  inventory: "Identifying the requested tests.",
  extracting: "Reading test requirements.",
  matching: "Comparing requirements with lab capability.",
  validating: "Checking the returned results.",
  saving: "Saving the checked results.",
};

/** Step counts are server observations, never a time-based completion estimate. */
export function inferenceStepDisplay(progress?: InferenceStepProgress | null) {
  if (!progress || typeof progress !== "object") return null;
  const stage = progress.stage;
  if (!Object.hasOwn(stageMessages, stage)) return null;
  const completed = progress.completed_steps;
  const total = progress.total_steps;
  const counted = Number.isInteger(completed) && completed >= 0;
  const planned =
    counted &&
    Number.isInteger(total) &&
    total !== null &&
    total > 0 &&
    completed <= total;
  const percent = progress.percent;
  const bounded =
    planned &&
    typeof percent === "number" &&
    Number.isFinite(percent) &&
    percent >= 0 &&
    percent <= 100 &&
    Math.abs(percent - (completed / total!) * 100) <= 1;
  const message =
    typeof progress.message === "string" && progress.message.trim()
      ? progress.message.trim().slice(0, 240)
      : stageMessages[stage];
  return {
    message,
    label: planned
      ? `AI steps completed ${completed}/${total}${bounded ? ` · ${Math.round(percent!)}%` : ""}`
      : counted
        ? `AI steps completed ${completed} · Planning total`
        : "Planning AI steps",
    percent: bounded ? percent : null,
    finalizing: stage === "saving" || (planned && completed === total),
  };
}

function samples(key: EstimateKey): DurationSample[] {
  try {
    const value: unknown = JSON.parse(
      localStorage.getItem(storageKey(key)) ?? "[]",
    );
    return Array.isArray(value)
      ? value
          .filter(
            (row): row is DurationSample =>
              row &&
              Number.isFinite(row.seconds) &&
              row.seconds >= 1 &&
              row.seconds <= 3600 &&
              Number.isFinite(row.units) &&
              row.units >= 1,
          )
          .slice(-12)
      : [];
  } catch {
    return [];
  }
}

/** Record observed, successful operations only. No content or provider keys are stored. */
export function recordInferenceDuration(
  key: EstimateKey,
  seconds: number,
  units = 1,
) {
  if (
    !Number.isFinite(seconds) ||
    seconds < 1 ||
    seconds > 3600 ||
    !Number.isFinite(units) ||
    units < 1
  )
    return;
  try {
    localStorage.setItem(
      storageKey(key),
      JSON.stringify(
        [...samples(key), { seconds, units: Math.max(1, units) }].slice(-12),
      ),
    );
  } catch {
    /* Timing history is optional; analysis never depends on browser storage. */
  }
}

function duration(seconds: number) {
  if (seconds < 60) return `${Math.max(10, Math.round(seconds / 10) * 10)} sec`;
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

/** Observed AI steps plus a separate, workload-matched timing reference. */
export default function InferenceProgress({
  title,
  detail,
  estimateKey = "matching",
  workUnits = 1,
  previousDurationSeconds,
  previousWorkUnits,
  startedAt,
  progress,
}: {
  title: string;
  detail: string;
  estimateKey?: EstimateKey;
  workUnits?: number;
  previousDurationSeconds?: number;
  previousWorkUnits?: number;
  startedAt?: string;
  progress?: InferenceStepProgress | null;
}) {
  const [elapsed, setElapsed] = useState(0);
  const steps = inferenceStepDisplay(progress);
  const estimate = useMemo(() => {
    const history = samples(estimateKey);
    const similar = history.filter(
      (row) => row.units >= workUnits / 2 && row.units <= workUnits * 2,
    );
    const values = similar.map((row) => row.seconds);
    if (
      !values.length &&
      previousDurationSeconds &&
      previousDurationSeconds >= 1 &&
      Number.isFinite(previousDurationSeconds) &&
      previousWorkUnits !== undefined &&
      Number.isFinite(previousWorkUnits) &&
      previousWorkUnits >= workUnits / 2 &&
      previousWorkUnits <= workUnits * 2
    )
      values.push(previousDurationSeconds);
    if (!values.length) return null;
    values.sort((a, b) => a - b);
    const low = Math.max(
      10,
      values[Math.floor((values.length - 1) * 0.25)] * 0.6,
    );
    const high = Math.max(
      low + 20,
      values[Math.ceil((values.length - 1) * 0.75)] * 1.8,
    );
    return { low, high, count: values.length };
  }, [estimateKey, workUnits, previousDurationSeconds, previousWorkUnits]);
  useEffect(() => {
    const parsed = startedAt ? Date.parse(startedAt) : NaN;
    const started = Number.isFinite(parsed) ? parsed : Date.now();
    setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)));
    const timer = window.setInterval(
      () => setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000))),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [startedAt]);
  return (
    <Box
      role="status"
      aria-label={title}
      sx={{
        p: 2,
        border: "1px solid #d9e4f6",
        borderRadius: 2,
        bgcolor: "#f5f8fe",
      }}
    >
      <Stack direction="row" gap={1.25} alignItems="center" sx={{ mb: 1.25 }}>
        <AutoAwesomeOutlinedIcon sx={{ color: "primary.main", fontSize: 22 }} />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography fontWeight={650} variant="body2">
            {title}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ overflowWrap: "anywhere" }}
          >
            {steps?.message ?? detail}
          </Typography>
        </Box>
        <Box sx={{ textAlign: "right", flexShrink: 0 }}>
          <Typography variant="caption" color="text.secondary" display="block">
            Elapsed
          </Typography>
          <Typography
            variant="body2"
            sx={{
              fontWeight: 600,
              whiteSpace: "nowrap",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
          </Typography>
        </Box>
      </Stack>
      {steps && (
        <Typography
          variant="caption"
          color="primary.main"
          sx={{ display: "block", mb: 0.75, fontWeight: 650 }}
        >
          {steps.label}
          {steps.finalizing &&
            " · Final checks and saving are still in progress"}
        </Typography>
      )}
      <LinearProgress
        aria-label="AI request in progress"
        variant={
          steps?.percent !== null && steps?.percent !== undefined
            ? "determinate"
            : "indeterminate"
        }
        value={steps?.percent ?? undefined}
        sx={{ height: 6, borderRadius: 5 }}
      />
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 1 }}
      >
        {estimate
          ? elapsed > estimate.high
            ? "Taking longer than previous runs. The request is still in progress."
            : `Reference time: ${duration(estimate.low)}–${duration(estimate.high)}, based on ${estimate.count === 1 ? "a comparable completed run" : `${estimate.count} comparable completed runs`}. Timing varies; this is not a countdown.`
          : "Several AI steps may be needed. Larger workloads can take several minutes; timing varies."}
      </Typography>
    </Box>
  );
}
