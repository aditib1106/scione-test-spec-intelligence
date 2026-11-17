import { Box, Stack, Typography } from "@mui/material";
import type { ComparisonCheck } from "../comparisonTypes";

const states = [
  { status: "supported", label: "matched", color: "#24745a" },
  { status: "gap", label: "gap", color: "#b34552" },
  { status: "unknown", label: "to clarify", color: "#315c9f" },
  { status: "source", label: "source check", color: "#a56817" },
] as const;

/** Counts checks, never changes the overall test assessment. Notes are not matches. */
export default function ComparisonCheckSummary({
  checks,
  label,
  showBar = false,
}: {
  checks: ComparisonCheck[];
  label: string;
  showBar?: boolean;
}) {
  const counts = states.map((state) => ({
    ...state,
    count: checks.filter((check) =>
      state.status === "source"
        ? check.status === "unknown" && check.kind === "source"
        : check.status === state.status &&
          !(state.status === "unknown" && check.kind === "source"),
    ).length,
  }));
  const total = counts.reduce((sum, state) => sum + state.count, 0);
  if (!total) return null;
  return (
    <Box role="group" aria-label={label} sx={{ minWidth: 0 }}>
      <Stack direction="row" gap={1.25} useFlexGap flexWrap="wrap">
        {counts
          .filter((state) => state.count > 0)
          .map((state) => (
            <Typography
              key={state.status}
              variant="caption"
              sx={{ color: state.color, fontWeight: 650, lineHeight: 1.65 }}
            >
              {state.count}{" "}
              {["gap", "source"].includes(state.status) && state.count !== 1
                ? `${state.label}s`
                : state.label}
            </Typography>
          ))}
      </Stack>
      {showBar && (
        <Box
          aria-hidden="true"
          sx={{
            display: "flex",
            gap: "2px",
            mt: 0.65,
            height: 4,
            borderRadius: 2,
            overflow: "hidden",
          }}
        >
          {counts
            .filter((state) => state.count > 0)
            .map((state) => (
              <Box
                key={state.status}
                sx={{ flex: state.count, bgcolor: state.color }}
              />
            ))}
        </Box>
      )}
    </Box>
  );
}
