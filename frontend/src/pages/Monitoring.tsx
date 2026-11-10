import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  MenuItem,
  Paper,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import InsightsRoundedIcon from "@mui/icons-material/InsightsRounded";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import TaskAltRoundedIcon from "@mui/icons-material/TaskAltRounded";
import FormatListBulletedRoundedIcon from "@mui/icons-material/FormatListBulletedRounded";
import { useResource } from "../api";
import { useAuth } from "../auth";

interface OperationSummary {
  count: number;
  success: number;
  failure: number;
  running: number;
  cache_reuse: number;
  incomplete?: number;
  duration_samples: number;
  median_ms: number | null;
  p95_ms: number | null;
}
interface Metrics {
  version: string;
  generated_at: string;
  scope: string;
  days: number;
  coverage: {
    lab_count: number;
    truncated: boolean;
    notices: string[];
    failure_history_complete?: boolean;
  };
  summary: {
    extraction: OperationSummary;
    matching: OperationSummary;
    volume: { documents: number; pages: number; tests: number };
    outcomes: Record<string, number>;
    source_review: {
      needs_review: number;
      total_tests: number;
      review_rate: number | null;
    };
  };
  daily: {
    date: string;
    extraction_count: number;
    matching_count: number;
    success: number;
    failure: number;
    pages: number;
    tests: number;
    mean_operation_ms: number | null;
  }[];
  recent: {
    id: string;
    kind: string;
    status: string;
    started_at: string;
    finished_at: string | null;
    duration_ms: number | null;
    provider_latency_ms: number | null;
    provider_calls: number | null;
    lab_id: string;
    lab_name: string;
    document_id: string;
    run_id: string | null;
    page_count: number | null;
    test_count: number | null;
    historical: boolean;
    duration_kind: string | null;
  }[];
}

const colors = { extraction: "#3565d4", matching: "#218579" };
const outcomes = [
  ["fully_matched", "Fully matched", "#298365"],
  ["partially_matched", "Partially matched", "#c28a30"],
  ["not_matched", "Not matched", "#c15461"],
  ["needs_information", "Needs information", "#7688a6"],
  ["excluded", "Excluded", "#bdc7d4"],
] as const;
const number = (value: number) => value.toLocaleString();
function duration(ms: number | null | undefined) {
  if (ms === null || ms === undefined || !Number.isFinite(ms))
    return "Not recorded";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} sec`;
  return `${(ms / 60_000).toFixed(1)} min`;
}
function dateLabel(value: string, time = false) {
  const date = new Date(time ? value : `${value.slice(0, 10)}T12:00:00`);
  return Number.isNaN(date.getTime())
    ? "Date not recorded"
    : date.toLocaleString(
        undefined,
        time
          ? {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            }
          : { month: "short", day: "numeric" },
      );
}
function Status({ value }: { value: string }) {
  const state = value.toLowerCase();
  const success = ["success", "succeeded", "completed"].includes(state);
  const failed = ["failure", "failed"].includes(state);
  const label = success
    ? "Succeeded"
    : failed
      ? "Failed"
      : state === "incomplete"
        ? "Completion not recorded"
        : state === "running"
          ? "Running"
          : ["cached", "cache_reuse"].includes(state)
            ? "Reused"
            : "Historical record";
  return (
    <Chip
      size="small"
      label={label}
      sx={{
        bgcolor: success ? "#e8f5ef" : failed ? "#fbecef" : "#edf1f7",
        color: success ? "#22694f" : failed ? "#a53749" : "#5e708c",
        fontWeight: 650,
      }}
    />
  );
}
function MetricCard({
  label,
  value,
  detail,
  note,
  icon,
}: {
  label: string;
  value: string;
  detail: string;
  note?: string;
  icon: React.ReactNode;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 2.25, borderRadius: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1 }}
      >
        <Typography variant="body2" color="text.secondary">
          {label}
        </Typography>
        <Box sx={{ color: "#5575b8", display: "flex" }}>{icon}</Box>
      </Stack>
      <Typography
        fontSize={29}
        fontWeight={750}
        letterSpacing="-.045em"
        lineHeight={1.2}
      >
        {value}
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 0.75 }}
      >
        {detail}
      </Typography>
      {note && (
        <Typography
          variant="caption"
          color="warning.main"
          sx={{ display: "block", mt: 0.5 }}
        >
          {note}
        </Typography>
      )}
    </Paper>
  );
}
function Activity({ daily }: { daily: Metrics["daily"] }) {
  if (
    !daily.length ||
    !daily.some((row) => row.extraction_count + row.matching_count > 0)
  )
    return (
      <Stack
        justifyContent="center"
        alignItems="center"
        sx={{ height: 210, color: "text.secondary" }}
      >
        <InsightsRoundedIcon sx={{ fontSize: 36, color: "#b9c8e2", mb: 1 }} />
        <Typography variant="body2">
          No recorded activity in this period.
        </Typography>
      </Stack>
    );
  const width = 680,
    height = 205,
    left = 30,
    bottom = 175,
    top = 16;
  const max = Math.max(
    1,
    ...daily.map((row) => Math.max(row.extraction_count, row.matching_count)),
  );
  const step = (width - left - 8) / daily.length;
  const barWidth = Math.min(24, step * 0.32);
  const y = (count: number) => bottom - (count / max) * (bottom - top);
  return (
    <Box sx={{ mt: 2 }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Recorded extraction and matching operations by day"
        style={{ display: "block", width: "100%" }}
      >
        {[...new Set([0, Math.ceil(max / 2), max])].map((tick) => (
          <g key={tick}>
            <line
              x1={left}
              x2={width - 8}
              y1={y(tick)}
              y2={y(tick)}
              stroke="#e7edf5"
              strokeDasharray={tick ? "3 4" : undefined}
            />
            <text
              x={left - 8}
              y={y(tick) + 4}
              textAnchor="end"
              fontSize="10"
              fill="#7c8ba1"
            >
              {tick}
            </text>
          </g>
        ))}
        {daily.map((row, index) => (
          <g key={row.date}>
            {(["extraction", "matching"] as const).map((kind, part) => {
              const count =
                kind === "extraction"
                  ? row.extraction_count
                  : row.matching_count;
              return (
                <rect
                  key={kind}
                  x={
                    left + step * (index + 0.5) + (part ? 1.5 : -barWidth - 1.5)
                  }
                  y={y(count)}
                  width={barWidth}
                  height={bottom - y(count)}
                  rx="2"
                  fill={colors[kind]}
                >
                  <title>
                    {dateLabel(row.date)}: {count} {kind} operations
                  </title>
                </rect>
              );
            })}
            {[0, Math.floor((daily.length - 1) / 2), daily.length - 1].includes(
              index,
            ) && (
              <text
                x={left + step * (index + 0.5)}
                y={bottom + 23}
                textAnchor="middle"
                fontSize="10"
                fill="#7c8ba1"
              >
                {dateLabel(row.date)}
              </text>
            )}
          </g>
        ))}
      </svg>
    </Box>
  );
}
function LatencyCard({
  label,
  summary,
  color,
}: {
  label: string;
  summary: OperationSummary;
  color: string;
}) {
  return (
    <Box
      sx={{
        p: 2,
        border: "1px solid #e3eaf3",
        borderLeft: `3px solid ${color}`,
        borderRadius: 1.5,
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="body2" fontWeight={650}>
          {label}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {summary.duration_samples} timed runs
        </Typography>
      </Stack>
      <Stack direction="row" sx={{ mt: 1.25 }}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Median
          </Typography>
          <Typography fontSize={21} fontWeight={700}>
            {duration(summary.median_ms)}
          </Typography>
        </Box>
        <Box sx={{ flex: 1, borderLeft: "1px solid #e8edf4", pl: 2 }}>
          <Tooltip title="95% of recorded durations are at or below this value.">
            <Typography variant="caption" color="text.secondary">
              P95
            </Typography>
          </Tooltip>
          <Typography fontSize={21} fontWeight={700}>
            {duration(summary.p95_ms)}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}

export default function Monitoring() {
  const { session } = useAuth();
  const hasLab = Boolean(session?.user?.lab_id) || session?.enabled === false;
  const [scope, setScope] = useState(hasLab ? "current" : "all");
  const [days, setDays] = useState(30);
  const { data, loading, error, refresh } = useResource<Metrics>(
    `/api/developer/metrics?scope=${scope}&days=${days}`,
  );
  const extraction = data?.summary.extraction;
  const metricsUnavailable = Boolean(
    data?.coverage.notices.some((notice) =>
      /metrics.*temporarily unavailable/i.test(notice),
    ),
  );
  const matching = data?.summary.matching;
  const total = (extraction?.count ?? 0) + (matching?.count ?? 0);
  const succeeded = (extraction?.success ?? 0) + (matching?.success ?? 0);
  const failed = (extraction?.failure ?? 0) + (matching?.failure ?? 0);
  const completed = succeeded + failed;
  const resultTotal = outcomes.reduce(
    (sum, [key]) => sum + (data?.summary.outcomes[key] ?? 0),
    0,
  );
  return (
    <Box sx={{ maxWidth: 1500, mx: "auto", p: { xs: 2, md: 3.5 } }}>
      <Stack
        direction={{ xs: "column", md: "row" }}
        justifyContent="space-between"
        alignItems={{ md: "center" }}
        gap={2}
        sx={{ mb: 3 }}
      >
        <Box>
          <Typography variant="overline" color="primary">
            Platform operations
          </Typography>
          <Typography variant="h4">Monitoring</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Recorded activity, processing time and review workload.
          </Typography>
        </Box>
        <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
          <TextField
            select
            label="Monitoring scope"
            size="small"
            value={scope}
            onChange={(event) => setScope(event.target.value)}
            sx={{ minWidth: 155 }}
          >
            <MenuItem value="current" disabled={!hasLab}>
              Current lab
            </MenuItem>
            <MenuItem value="all">All labs</MenuItem>
          </TextField>
          <TextField
            select
            label="Period"
            size="small"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
            sx={{ minWidth: 140 }}
          >
            <MenuItem value={7}>Last 7 days</MenuItem>
            <MenuItem value={30}>Last 30 days</MenuItem>
          </TextField>
          <Button
            variant="outlined"
            startIcon={
              loading ? <CircularProgress size={15} /> : <RefreshRoundedIcon />
            }
            disabled={loading}
            onClick={refresh}
          >
            Refresh
          </Button>
        </Stack>
      </Stack>
      {error && (
        <Alert
          severity="error"
          action={<Button onClick={refresh}>Retry</Button>}
          sx={{ mb: 2 }}
        >
          {error}
        </Alert>
      )}
      {loading && !data && (
        <Stack gap={2} aria-label="Loading monitoring">
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr 1fr", lg: "repeat(4, 1fr)" },
              gap: 2,
            }}
          >
            {[0, 1, 2, 3].map((key) => (
              <Skeleton key={key} variant="rounded" height={127} />
            ))}
          </Box>
          <Skeleton variant="rounded" height={290} />
        </Stack>
      )}
      {data && (
        <Stack gap={2.5}>
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
            <Typography variant="caption" color="text.secondary">
              {scope === "all"
                ? `${data.coverage.lab_count} lab workspaces`
                : session?.user?.lab_name || "Current lab"}{" "}
              · Updated {dateLabel(data.generated_at, true)}
            </Typography>
            {data.coverage.truncated && (
              <Chip size="small" color="warning" label="Partial coverage" />
            )}
          </Stack>
          {data.coverage.notices.length > 0 && (
            <Alert
              severity={
                data.coverage.truncated || metricsUnavailable
                  ? "warning"
                  : "info"
              }
            >
              {data.coverage.notices.join(" ")}
            </Alert>
          )}
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr 1fr", lg: "repeat(4, 1fr)" },
              gap: 2,
            }}
          >
            <MetricCard
              label="Recorded operations"
              value={number(total)}
              detail={`${number(extraction!.count)} extraction · ${number(matching!.count)} matching`}
              icon={<InsightsRoundedIcon fontSize="small" />}
            />
            <MetricCard
              label="Recorded success"
              value={
                completed
                  ? `${Math.round((succeeded / completed) * 100)}%`
                  : "—"
              }
              detail={`${number(succeeded)} succeeded · ${number(failed)} failed`}
              note={
                data.coverage.failure_history_complete === false
                  ? "Historical failures may be incomplete."
                  : undefined
              }
              icon={<TaskAltRoundedIcon fontSize="small" />}
            />
            <MetricCard
              label="Pages processed"
              value={number(data.summary.volume.pages)}
              detail={`${number(data.summary.volume.documents)} customer documents`}
              icon={<DescriptionOutlinedIcon fontSize="small" />}
            />
            <MetricCard
              label="Extracted tests"
              value={number(data.summary.volume.tests)}
              detail={`${number(data.summary.source_review.needs_review)} currently need source review`}
              icon={<FormatListBulletedRoundedIcon fontSize="small" />}
            />
          </Box>
          {total === 0 && (
            <Alert
              severity={metricsUnavailable ? "warning" : "info"}
              action={
                metricsUnavailable ? (
                  <Button color="inherit" onClick={refresh}>
                    Retry
                  </Button>
                ) : undefined
              }
            >
              {metricsUnavailable
                ? "Some workspace metrics could not be loaded. Retry to refresh this summary."
                : "No operations recorded in this period. New extraction and comparison runs will appear here."}
            </Alert>
          )}
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", lg: "1.65fr 1fr" },
              gap: 2.5,
            }}
          >
            <Paper variant="outlined" sx={{ p: 2.5, borderRadius: 2 }}>
              <Stack
                direction="row"
                justifyContent="space-between"
                gap={2}
                alignItems="center"
                flexWrap="wrap"
              >
                <Box>
                  <Typography variant="subtitle1">Activity</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Recorded operations by day
                  </Typography>
                </Box>
                <Stack direction="row" gap={2}>
                  {(["extraction", "matching"] as const).map((kind) => (
                    <Stack
                      key={kind}
                      direction="row"
                      gap={0.75}
                      alignItems="center"
                    >
                      <Box
                        sx={{
                          width: 8,
                          height: 8,
                          bgcolor: colors[kind],
                          borderRadius: 0.5,
                        }}
                      />
                      <Typography variant="caption" color="text.secondary">
                        {kind === "extraction" ? "Extraction" : "Matching"}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              </Stack>
              <Activity daily={data.daily} />
            </Paper>
            <Paper variant="outlined" sx={{ p: 2.5, borderRadius: 2 }}>
              <Typography variant="subtitle1">Processing time</Typography>
              <Typography variant="caption" color="text.secondary">
                Recorded end-to-end duration
              </Typography>
              <Stack gap={1.5} sx={{ mt: 2 }}>
                <LatencyCard
                  label="Extraction"
                  summary={data.summary.extraction}
                  color={colors.extraction}
                />
                <LatencyCard
                  label="Matching"
                  summary={data.summary.matching}
                  color={colors.matching}
                />
              </Stack>
            </Paper>
          </Box>
          <Paper variant="outlined" sx={{ p: 2.5, borderRadius: 2 }}>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              gap={1}
              justifyContent="space-between"
            >
              <Box>
                <Typography variant="subtitle1">
                  Saved comparison results
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Latest saved result per document. Capability outcomes describe
                  the request and lab declarations, not model accuracy.
                </Typography>
              </Box>
              <Typography variant="body2" fontWeight={650}>
                {number(resultTotal)} test items
              </Typography>
            </Stack>
            {resultTotal > 0 ? (
              <Box
                role="img"
                aria-label="Saved capability comparison outcome distribution"
                sx={{
                  display: "flex",
                  overflow: "hidden",
                  height: 13,
                  borderRadius: 4,
                  my: 2,
                }}
              >
                {outcomes.map(([key, label, color]) => {
                  const count = data.summary.outcomes[key] ?? 0;
                  return count ? (
                    <Tooltip key={key} title={`${label}: ${count}`}>
                      <Box
                        sx={{
                          width: `${(count / resultTotal) * 100}%`,
                          bgcolor: color,
                        }}
                      />
                    </Tooltip>
                  ) : null;
                })}
              </Box>
            ) : (
              <Typography variant="body2" color="text.secondary" sx={{ my: 2 }}>
                No saved comparison results in this period.
              </Typography>
            )}
            <Stack direction="row" gap={2.5} useFlexGap flexWrap="wrap">
              {outcomes.map(([key, label, color]) => (
                <Stack key={key} direction="row" gap={0.75} alignItems="center">
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      bgcolor: color,
                    }}
                  />
                  <Typography variant="caption" color="text.secondary">
                    {label}
                  </Typography>
                  <Typography variant="caption" fontWeight={700}>
                    {number(data.summary.outcomes[key] ?? 0)}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </Paper>
          <Paper
            variant="outlined"
            sx={{ borderRadius: 2, overflow: "hidden" }}
          >
            <Stack
              direction="row"
              justifyContent="space-between"
              sx={{ p: 2.5 }}
            >
              <Typography variant="subtitle1">Recent operations</Typography>
              <Typography variant="caption" color="text.secondary">
                {data.recent.length} records
              </Typography>
            </Stack>
            {!data.recent.length ? (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ px: 2.5, pb: 3 }}
              >
                No recent operations to display.
              </Typography>
            ) : (
              <TableContainer>
                <Table size="small" aria-label="Recent operations">
                  <TableHead>
                    <TableRow>
                      {[
                        "Started",
                        "Operation",
                        "Lab",
                        "Workload",
                        "Duration",
                        "Result",
                      ].map((label) => (
                        <TableCell key={label}>{label}</TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {data.recent.map((row) => (
                      <TableRow
                        key={row.id}
                        sx={{ "&:last-child td": { borderBottom: 0 } }}
                      >
                        <TableCell sx={{ whiteSpace: "nowrap" }}>
                          {dateLabel(row.started_at, true)}
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" fontWeight={600}>
                            {row.kind === "extraction"
                              ? "Extraction"
                              : "Matching"}
                          </Typography>
                          {row.historical && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              Historical record
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell>
                          {row.lab_name || "Lab not recorded"}
                        </TableCell>
                        <TableCell sx={{ whiteSpace: "nowrap" }}>
                          {row.test_count === null
                            ? "—"
                            : `${row.test_count} tests`}
                          {row.page_count !== null && (
                            <Typography
                              variant="caption"
                              display="block"
                              color="text.secondary"
                            >
                              {row.page_count} pages
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell sx={{ whiteSpace: "nowrap" }}>
                          <Typography variant="body2">
                            {duration(row.duration_ms)}
                          </Typography>
                          {row.duration_ms === null &&
                            row.provider_latency_ms !== null && (
                              <Typography
                                variant="caption"
                                display="block"
                                color="text.secondary"
                              >
                                Provider time (sum):{" "}
                                {duration(row.provider_latency_ms)}
                              </Typography>
                            )}
                        </TableCell>
                        <TableCell>
                          <Status value={row.status} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Paper>
          <Typography variant="caption" color="text.secondary">
            Recorded success covers retained, completed operations, not output
            accuracy. Reused results and unfinished operations are excluded from
            that rate. Missing end-to-end timings stay unavailable; provider
            sums are shown separately. This page never starts inference or
            refreshes automatically.
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
