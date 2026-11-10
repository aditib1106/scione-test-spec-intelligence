import { useEffect, useMemo, useState } from "react";
import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";

type EstimateKey = "analysis" | "matching";
interface DurationSample {
  seconds: number;
  units: number;
}
const storageKey = (key: EstimateKey) => `scione:completed-duration:${key}:v1`;

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
  if (!Number.isFinite(seconds) || seconds < 1 || seconds > 3600) return;
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

/** Indeterminate progress and a coarse reference based on completed local runs. */
export default function InferenceProgress({
  title,
  detail,
  estimateKey = "matching",
  workUnits = 1,
  previousDurationSeconds,
}: {
  title: string;
  detail: string;
  estimateKey?: EstimateKey;
  workUnits?: number;
  previousDurationSeconds?: number;
}) {
  const [elapsed, setElapsed] = useState(0);
  const estimate = useMemo(() => {
    const history = samples(estimateKey);
    const similar = history.filter(
      (row) => row.units >= workUnits / 2 && row.units <= workUnits * 2,
    );
    const values = (similar.length ? similar : history).map(
      (row) => row.seconds,
    );
    if (
      !values.length &&
      previousDurationSeconds &&
      previousDurationSeconds >= 1
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
  }, [estimateKey, workUnits, previousDurationSeconds]);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, []);
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
            {detail}
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
      <LinearProgress
        aria-label="AI request in progress"
        sx={{ height: 4, borderRadius: 5 }}
      />
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 1 }}
      >
        {estimate
          ? elapsed > estimate.high
            ? "Taking longer than previous runs. The request is still in progress."
            : `Reference time: ${duration(estimate.low)}–${duration(estimate.high)}, based on ${estimate.count === 1 ? "a previous completed run" : `${estimate.count} completed runs`}. Larger workloads may take longer.`
          : "No previous run to estimate yet. The elapsed time will keep updating."}
      </Typography>
    </Box>
  );
}
