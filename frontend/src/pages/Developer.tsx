import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import { request, useResource } from "../api";
import type { PageProps, SpecificationData } from "../types";

type Row = Record<string, unknown>;
interface Diagnostics {
  runs: Row[];
  selected: SpecificationData;
  summary: Row | null;
  requirements: Row[];
  evidence: Row[];
  comparison: Row[];
}

function DataTable({ rows }: { rows: Row[] }) {
  if (!rows.length)
    return <Alert severity="info">No records to display.</Alert>;
  const columns = Object.keys(rows[0]);
  return (
    <TableContainer sx={{ maxHeight: "65vh" }}>
      <Table stickyHeader size="small">
        <TableHead>
          <TableRow>
            {columns.map((key) => (
              <TableCell key={key} sx={{ whiteSpace: "nowrap" }}>
                {key}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {columns.map((key) => (
                <TableCell
                  key={key}
                  sx={{
                    minWidth: 100,
                    maxWidth: 400,
                    verticalAlign: "top",
                    overflowWrap: "anywhere",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {row[key] === null || row[key] === undefined
                    ? "—"
                    : typeof row[key] === "object"
                      ? JSON.stringify(row[key])
                      : String(row[key])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

export default function Developer({ workspace, onRefresh }: PageProps) {
  const [params, setParams] = useSearchParams();
  const documentId =
    params.get("document") ?? workspace.documents[0]?.document_id ?? "";
  const [runId, setRunId] = useState("");
  const [baseline, setBaseline] = useState("");
  const [tab, setTab] = useState(0);
  const [resetOpen, setResetOpen] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [notice, setNotice] = useState("");
  const [resetError, setResetError] = useState("");
  const query = new URLSearchParams();
  if (runId) query.set("run_id", runId);
  if (baseline) query.set("baseline_id", baseline);
  const { data, loading, error, refresh } = useResource<Diagnostics>(
    documentId ? `/api/diagnostics/${documentId}?${query}` : null,
  );
  useEffect(() => {
    setRunId("");
    setBaseline("");
    setTab(0);
  }, [documentId]);
  async function reset() {
    setResetting(true);
    setResetError("");
    try {
      const result = await request<{ backup_path: string | null }>(
        "/api/workspace/reset",
        { method: "POST", body: JSON.stringify({ acknowledged }) },
      );
      setNotice(
        result.backup_path
          ? `Workspace reset. Recovery backup: ${result.backup_path}`
          : "Workspace reset. No saved data needed moving.",
      );
      setResetOpen(false);
      setParams({});
      onRefresh();
      refresh();
    } catch (reason) {
      setResetError(reason instanceof Error ? reason.message : "Reset failed.");
    } finally {
      setResetting(false);
    }
  }
  const selectedRun = data?.selected.run;
  return (
    <Box sx={{ maxWidth: 1440, mx: "auto", p: { xs: 2, md: 4 } }}>
      <Typography variant="overline" color="primary">
        Diagnostics
      </Typography>
      <Typography variant="h4">Developer Tools</Typography>
      <Typography color="text.secondary" sx={{ mt: 1, mb: 3 }}>
        Inspect saved outputs, local checks and run history. Opening this page
        makes no model requests.
      </Typography>
      {notice && (
        <Alert severity="success" sx={{ mb: 2 }}>
          {notice}
        </Alert>
      )}
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
          <TextField
            select
            label="Specification"
            value={documentId}
            sx={{ flex: 1 }}
            disabled={!workspace.documents.length}
            onChange={(event) => {
              setRunId("");
              setBaseline("");
              setParams({ document: event.target.value });
            }}
          >
            <MenuItem value="">No saved specifications</MenuItem>
            {workspace.documents.map((doc) => (
              <MenuItem key={doc.document_id} value={doc.document_id}>
                {doc.original_name}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Saved run"
            value={runId || selectedRun?.run_id || ""}
            sx={{ flex: 1 }}
            disabled={!data?.runs.length}
            onChange={(event) => setRunId(event.target.value)}
          >
            <MenuItem value="">No saved runs</MenuItem>
            {data?.runs.map((run) => (
              <MenuItem key={String(run.Run)} value={String(run.Run)}>
                {String(run.Model)} ·{" "}
                {new Date(String(run.Started)).toLocaleString()}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      </Paper>
      {error && (
        <Alert
          severity="error"
          action={<Button onClick={refresh}>Retry</Button>}
        >
          {error}
        </Alert>
      )}
      {loading && <CircularProgress size={24} sx={{ m: 2 }} />}
      {!documentId ? (
        <Alert severity="info">
          No specifications saved yet. Import a PDF in Specifications to begin.
        </Alert>
      ) : (
        !selectedRun &&
        !loading && (
          <Alert severity="info">
            No saved extraction for this document. Analyze it in Specifications
            first.
          </Alert>
        )
      )}
      {data && selectedRun && (
        <Paper variant="outlined" sx={{ overflow: "hidden" }}>
          <Tabs
            value={tab}
            onChange={(_, value: number) => setTab(value)}
            variant="scrollable"
            allowScrollButtonsMobile
          >
            <Tab label="Overview" />
            <Tab label="Requirements" />
            <Tab label="Evidence & checks" />
            <Tab label="Compare runs" />
            <Tab label="Raw & metadata" />
          </Tabs>
          <Box sx={{ p: { xs: 1.5, md: 3 } }}>
            {tab === 0 && (
              <>
                <Stack direction="row" flexWrap="wrap" gap={2} sx={{ mb: 3 }}>
                  {[
                    ["Records", selectedRun.extraction.requirements.length],
                    [
                      "Need source review",
                      Object.values(data.selected.assessments).filter(
                        (item) => item.state === "needs_attention",
                      ).length,
                    ],
                    [
                      "Latency",
                      `${(selectedRun.latency_ms / 1000).toFixed(2)}s`,
                    ],
                    ["Total tokens", selectedRun.usage.total_tokens ?? "—"],
                  ].map(([label, value]) => (
                    <Box
                      key={label}
                      sx={{
                        flex: 1,
                        minWidth: 130,
                        p: 2,
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: 1.5,
                      }}
                    >
                      <Typography variant="caption" color="text.secondary">
                        {label}
                      </Typography>
                      <Typography variant="h5" sx={{ mt: 1 }}>
                        {value}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
                <DataTable
                  rows={Object.entries(data.summary ?? {}).map(
                    ([key, value]) => ({ Field: key, Value: value }),
                  )}
                />
                <Alert severity="info" sx={{ mt: 2 }}>
                  Counts and source-location checks are not accuracy scores or
                  verified lab support.
                </Alert>
              </>
            )}
            {tab === 1 && <DataTable rows={data.requirements} />}
            {tab === 2 && <DataTable rows={data.evidence} />}
            {tab === 3 && (
              <Stack spacing={2}>
                <TextField
                  select
                  label="Baseline run"
                  value={baseline}
                  onChange={(event) => setBaseline(event.target.value)}
                >
                  <MenuItem value="">Select a saved run</MenuItem>
                  {data.runs.map((run) => (
                    <MenuItem key={String(run.Run)} value={String(run.Run)}>
                      {String(run.Model)} · {String(run.Run).slice(0, 8)}
                    </MenuItem>
                  ))}
                </TextField>
                <Typography variant="body2" color="text.secondary">
                  Comparison is descriptive. Neither run is ground truth;
                  repeated signatures remain ambiguous.
                </Typography>
                <DataTable rows={data.comparison} />
              </Stack>
            )}
            {tab === 4 && (
              <Stack spacing={2}>
                <Stack direction="row" gap={1} flexWrap="wrap">
                  <Button
                    component="a"
                    href={`/api/diagnostics/${documentId}/raw?run_id=${encodeURIComponent(selectedRun.run_id)}`}
                    variant="outlined"
                  >
                    Download untouched provider text
                  </Button>
                  <Button
                    onClick={() => {
                      const url = URL.createObjectURL(
                        new Blob([JSON.stringify(selectedRun, null, 2)], {
                          type: "application/json",
                        }),
                      );
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `run-${selectedRun.run_id}.json`;
                      a.click();
                      setTimeout(() => URL.revokeObjectURL(url), 1000);
                    }}
                  >
                    Download expanded run JSON
                  </Button>
                </Stack>
                <Typography variant="caption" color="text.secondary">
                  Coverage-pass top-level text may be a local manifest; per-call
                  responses remain in the expanded run.
                </Typography>
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    p: 2,
                    bgcolor: "#f7f9fc",
                    borderRadius: 1,
                    overflow: "auto",
                    maxHeight: "60vh",
                    fontSize: 12,
                  }}
                >
                  {JSON.stringify(selectedRun, null, 2)}
                </Box>
              </Stack>
            )}
          </Box>
        </Paper>
      )}
      <Accordion
        elevation={0}
        sx={{
          mt: 4,
          border: "1px solid",
          borderColor: "divider",
          "&:before": { display: "none" },
        }}
      >
        <AccordionSummary expandIcon={<ExpandMoreRounded />}>
          <Typography variant="body2" color="text.secondary">
            Workspace maintenance
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Return to first use by moving lab entries, uploads, analyses,
            reviews and reports into a recovery backup. Credentials and source
            assets stay in place.
          </Typography>
          <Button
            color="error"
            variant="outlined"
            onClick={() => {
              setResetOpen(true);
              setAcknowledged(false);
              setResetError("");
            }}
          >
            Reset demo workspace
          </Button>
        </AccordionDetails>
      </Accordion>
      <Dialog
        open={resetOpen}
        onClose={resetting ? undefined : () => setResetOpen(false)}
        maxWidth="sm"
      >
        <DialogTitle>Reset demo workspace?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            Finish any running analysis first. Saved state will be moved to a
            private recovery backup.
          </Typography>
          {resetError && <Alert severity="error">{resetError}</Alert>}
          <FormControlLabel
            control={
              <Checkbox
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
            }
            label="I want to back up and clear this local demo"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setResetOpen(false)} disabled={resetting}>
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            disabled={resetting || !acknowledged}
            onClick={() => void reset()}
          >
            {resetting ? "Resetting…" : "Reset to first use"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
