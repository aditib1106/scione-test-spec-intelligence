import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import {
  Alert,
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  MenuItem,
  Paper,
  Radio,
  RadioGroup,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import DownloadRoundedIcon from "@mui/icons-material/DownloadRounded";
import { request } from "../api";
import type {
  GapLine,
  MappingChoice,
  PageProps,
  ReportData,
  ReportSnapshot,
} from "../types";

type SnapshotInfo = { page_count: number; report: ReportSnapshot };
type TabName = "review" | "report" | "developer";
type FilterName =
  "Needs review" | "All active" | "Methods listed" | "Gaps" | "Excluded";
const blue = "#2459cc";
const panelSx = { borderColor: "#dce3ee", borderRadius: 2, bgcolor: "#fff" };
const formatLabel = (value: string) =>
  value.replaceAll("_", " ").replace(/^\w/, (char) => char.toUpperCase());
const idOf = (line: GapLine) => line.requirement.id;
const joinOr = (values: string[], fallback = "Not stated") =>
  values.length ? values.join(", ") : fallback;
const methodCodes = (line: GapLine) => [
  ...new Set(line.requirement.requested_methods.map((method) => method.code)),
];

function isPending(data: ReportData, line: GapLine) {
  const state = data.line_states[idOf(line)];
  return line.status !== "excluded" && Boolean(state?.needs_review);
}

function SummaryCard({
  label,
  count,
  tint,
}: {
  label: string;
  count: number;
  tint: string;
}) {
  return (
    <Card
      variant="outlined"
      sx={{ ...panelSx, flex: 1, minWidth: 150, bgcolor: tint }}
    >
      <CardContent sx={{ "&:last-child": { pb: 1.6 }, p: 1.8 }}>
        <Typography variant="body2" color="text.secondary">
          {label}
        </Typography>
        <Typography variant="h4" fontWeight={700} sx={{ mt: 0.4 }}>
          {count}
        </Typography>
      </CardContent>
    </Card>
  );
}

function ValueList({
  title,
  values,
}: {
  title: string;
  values: {
    name: string;
    raw_value: string;
    unit?: string | null;
    context?: string | null;
  }[];
}) {
  return (
    <Box sx={{ mb: 1.4 }}>
      <Typography variant="subtitle2" fontWeight={700}>
        {title}
      </Typography>
      {values.length ? (
        <Stack spacing={0.6} sx={{ mt: 0.6 }}>
          {values.map((value, index) => (
            <Typography variant="body2" key={`${value.name}-${index}`}>
              {value.name}: {value.raw_value}
              {value.unit ? ` [${value.unit}]` : ""}
              {value.context ? ` · ${value.context}` : ""}
            </Typography>
          ))}
        </Stack>
      ) : (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          Not stated in the extracted content.
        </Typography>
      )}
    </Box>
  );
}

function statusText(data: ReportData, line: GapLine) {
  const state = data.line_states[idOf(line)];
  if (line.extraction_review_state === "needs_attention")
    return "Check extracted text in Specifications";
  if (line.status === "excluded") return "Excluded from this request";
  if (line.status === "needs_review")
    return line.candidate_test_ids.length
      ? "Choose the correct test or method"
      : "No catalog match yet";
  if (line.status === "test_gap") return "Gap · test not listed by your lab";
  if (line.status === "method_gap") return "Gap · requested method not listed";
  if (line.status === "condition_gap") return "Gap · outside your lab scope";
  if (state?.support_basis === "human") return "Lab support confirmed";
  if (state?.support_basis === "automatic")
    return "Operating conditions matched automatically";
  return "Check remaining operating requirements";
}

export default function Reports({ workspace, onRefresh }: PageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const routeDocumentId = searchParams.get("document");
  const [selectedDocument, setSelectedDocument] = useState("");
  const documentId =
    routeDocumentId ||
    selectedDocument ||
    workspace.documents[0]?.document_id ||
    "";
  const activeDocument = useRef(documentId);
  activeDocument.current = documentId;
  const [data, setData] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(Boolean(documentId));
  const [error, setError] = useState("");
  const [tab, setTab] = useState<TabName>("review");
  const [filter, setFilter] = useState<FilterName>("Needs review");
  const [advanceAfterSave, setAdvanceAfterSave] = useState(true);
  const [selectedId, setSelectedId] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [scopeNote, setScopeNote] = useState("");
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [batchAck, setBatchAck] = useState(false);
  const [batchDecision, setBatchDecision] = useState("");
  const [batchNote, setBatchNote] = useState("");
  const [mappingOpen, setMappingOpen] = useState(false);
  const [mappingTest, setMappingTest] = useState("");
  const [mappingMethods, setMappingMethods] = useState<string[]>([]);
  const [mappingCapability, setMappingCapability] = useState("");
  const [mappingVerdict, setMappingVerdict] =
    useState<MappingChoice["capability_verdict"]>("unreviewed");
  const [mappingNote, setMappingNote] = useState("");
  const [quantity, setQuantity] = useState("");
  const [quantityNote, setQuantityNote] = useState("");
  const [quantityAck, setQuantityAck] = useState(false);
  const [snapshot, setSnapshot] = useState<SnapshotInfo | null>(null);
  const [snapshotId, setSnapshotId] = useState("");
  const [snapshotPage, setSnapshotPage] = useState(1);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const snapshotRequest = useRef(0);

  const load = useCallback(async (id: string, quiet = false) => {
    if (!id) {
      setData(null);
      setLoading(false);
      return;
    }
    if (!quiet) setLoading(true);
    setError("");
    try {
      const result = await request<ReportData>(
        `/api/reports/${encodeURIComponent(id)}`,
      );
      if (activeDocument.current === id) setData(result);
    } catch (reason) {
      if (activeDocument.current === id) {
        setData(null);
        setError(
          reason instanceof Error
            ? reason.message
            : "The report could not be loaded.",
        );
      }
    } finally {
      if (activeDocument.current === id) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setData(null);
    setSelectedId("");
    setSnapshot(null);
    setSnapshotId("");
    setSnapshotLoading(false);
    setBusy(false);
    setNotice("");
    setBatchOpen(false);
    setBatchIds([]);
    setTab("review");
    snapshotRequest.current += 1;
    void load(documentId ?? "");
  }, [documentId, load]);
  useEffect(() => {
    if (routeDocumentId) setSelectedDocument(routeDocumentId);
    else if (
      !workspace.documents.some((item) => item.document_id === selectedDocument)
    )
      setSelectedDocument(workspace.documents[0]?.document_id ?? "");
  }, [routeDocumentId, selectedDocument, workspace.documents]);

  const lines = data?.report.lines ?? [];
  const currentLine = lines.find((line) => idOf(line) === selectedId) ?? null;
  const gapCount = lines.filter((line) =>
    ["test_gap", "method_gap", "condition_gap"].includes(line.status),
  ).length;
  const pendingCount = data
    ? lines.filter((line) => isPending(data, line)).length
    : 0;
  const resolvedCount = data
    ? lines.filter((line) =>
        ["automatic", "human"].includes(
          data.line_states[idOf(line)]?.support_basis ?? "",
        ),
      ).length
    : 0;
  const visibleLines = useMemo(() => {
    if (!data) return [];
    return lines.filter((line) =>
      filter === "All active"
        ? line.status !== "excluded"
        : filter === "Methods listed"
          ? line.status === "in_house"
          : filter === "Gaps"
            ? ["test_gap", "method_gap", "condition_gap"].includes(line.status)
            : filter === "Excluded"
              ? line.status === "excluded"
              : isPending(data, line),
    );
  }, [data, lines, filter]);
  useEffect(() => {
    if (!visibleLines.some((line) => idOf(line) === selectedId))
      setSelectedId(visibleLines[0] ? idOf(visibleLines[0]) : "");
  }, [visibleLines, selectedId]);

  useEffect(() => {
    if (!currentLine || !data) return;
    const choice = currentLine.mapping_choice;
    setMappingTest(
      choice?.canonical_test_id ?? currentLine.canonical_test_id ?? "",
    );
    setMappingMethods(
      choice?.selected_method_codes?.filter((code) =>
        methodCodes(currentLine).includes(code),
      ) ?? methodCodes(currentLine),
    );
    setMappingCapability(
      choice?.capability_id ?? currentLine.capability?.id ?? "",
    );
    setMappingVerdict(choice?.capability_verdict ?? "unreviewed");
    setMappingNote(choice?.note ?? "");
    setScopeNote("");
    const savedQuantity = data.report.workspace.quantities[idOf(currentLine)];
    setQuantity(savedQuantity?.quantity ?? "");
    setQuantityNote(savedQuantity?.note ?? "");
    setQuantityAck(false);
    setMappingOpen(currentLine.status === "needs_review");
  }, [currentLine, data?.report.workspace.version]);

  const base = data
    ? {
        run_id: data.report.source_run.run_id,
        version: data.report.workspace.version,
        input_fingerprint: data.report.input_fingerprint,
      }
    : null;
  async function mutate(
    path: string,
    body: unknown,
    success: string,
  ): Promise<ReportData | null> {
    if (!documentId || busy) return null;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await request<ReportData>(path, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (activeDocument.current === documentId) {
        setData(updated);
        setNotice(success);
        onRefresh();
        return updated;
      }
    } catch (reason) {
      if (activeDocument.current === documentId) {
        setError(
          reason instanceof Error
            ? reason.message
            : "The action could not be completed.",
        );
      }
    } finally {
      if (activeDocument.current === documentId) setBusy(false);
    }
    return null;
  }
  async function support(line: GapLine, supported: boolean, note: string) {
    if (!data || !base) return;
    const updated = await mutate(
      `/api/reports/${encodeURIComponent(data.report.document_id)}/scope`,
      {
        ...base,
        requirement_ids: [idOf(line)],
        supported,
        note: note.trim(),
        acknowledged: true,
      },
      supported ? "Lab support review saved." : "Limitation saved.",
    );
    if (!updated) return;
    if (!advanceAfterSave) {
      if (filter === "Needs review" && !isPending(updated, line))
        setFilter("All active");
      return;
    }
    const order = lines.map(idOf);
    const start = order.indexOf(idOf(line));
    const candidates = order.slice(start + 1).concat(order.slice(0, start));
    const next = candidates.find((id) => {
      const candidate = lines.find((item) => idOf(item) === id);
      return Boolean(candidate && isPending(updated, candidate));
    });
    if (next) setSelectedId(next);
  }
  async function saveMapping() {
    if (!data || !currentLine || !base) return;
    const original = methodCodes(currentLine);
    if (original.length && !mappingMethods.length) {
      setError("Keep at least one method from the customer request.");
      return;
    }
    if (
      original.some((code) => !mappingMethods.includes(code)) &&
      !mappingNote.trim()
    ) {
      setError("Explain why a requested method does not apply.");
      return;
    }
    if (mappingVerdict === "unsupported" && !mappingNote.trim()) {
      setError("Describe the limitation before recording it.");
      return;
    }
    const changedMatch =
      mappingTest !==
        (currentLine.mapping_choice?.canonical_test_id ??
          currentLine.canonical_test_id ??
          "") ||
      JSON.stringify([...mappingMethods].sort()) !==
        JSON.stringify(
          [
            ...(currentLine.mapping_choice?.selected_method_codes ??
              currentLine.considered_method_codes),
          ].sort(),
        );
    const verdict =
      changedMatch && mappingVerdict === "supported"
        ? "unreviewed"
        : mappingVerdict;
    await mutate(
      `/api/reports/${encodeURIComponent(data.report.document_id)}/mapping`,
      {
        ...base,
        requirement_id: idOf(currentLine),
        mapping: {
          canonical_test_id: mappingTest || null,
          selected_method_codes: original.length ? mappingMethods : null,
          capability_id: mappingCapability || null,
          capability_verdict: verdict,
          note: mappingNote.trim(),
        },
      },
      verdict === "unreviewed"
        ? "Matching saved for review."
        : "Matching review saved.",
    );
  }
  async function loadSnapshot(id: string) {
    const requestId = ++snapshotRequest.current;
    setSnapshotId(id);
    setSnapshot(null);
    setSnapshotLoading(true);
    try {
      const result = await request<SnapshotInfo>(
        `/api/snapshots/${encodeURIComponent(id)}`,
      );
      if (requestId === snapshotRequest.current) {
        setSnapshot(result);
        setSnapshotPage(1);
      }
    } catch (reason) {
      if (requestId === snapshotRequest.current)
        setError(
          reason instanceof Error
            ? reason.message
            : "Saved report could not be opened.",
        );
    } finally {
      if (requestId === snapshotRequest.current) setSnapshotLoading(false);
    }
  }
  async function createSnapshot() {
    if (!data) return;
    const documentAtSubmit = documentId;
    setBusy(true);
    setError("");
    try {
      const result = await request<{ report_id: string }>(
        `/api/reports/${encodeURIComponent(data.report.document_id)}/snapshots`,
        {
          method: "POST",
          body: JSON.stringify({
            run_id: data.report.source_run.run_id,
            input_fingerprint: data.report.input_fingerprint,
          }),
        },
      );
      if (activeDocument.current !== documentAtSubmit) return;
      await load(documentAtSubmit, true);
      if (activeDocument.current !== documentAtSubmit) return;
      await loadSnapshot(result.report_id);
      if (activeDocument.current !== documentAtSubmit) return;
      onRefresh();
      setNotice("Draft report saved. Preview or download it below.");
    } catch (reason) {
      if (activeDocument.current === documentAtSubmit) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Draft report could not be created.",
        );
      }
    } finally {
      if (activeDocument.current === documentAtSubmit) setBusy(false);
    }
  }
  useEffect(() => {
    if (
      tab === "report" &&
      data?.report.document_id === documentId &&
      !snapshotId &&
      data.saved_reports.length
    ) {
      void loadSnapshot(data.saved_reports[0].report_id);
    }
  }, [tab, documentId, data?.saved_reports, snapshotId]);
  const selectedSaved = data?.saved_reports.find(
    (item) => item.report_id === snapshotId,
  );
  const currentSnapshot = Boolean(selectedSaved?.current);
  const pendingBatch = data
    ? batchIds.filter((id) => {
        const line = lines.find((item) => idOf(item) === id);
        return Boolean(line && data.line_states[id]?.batch_eligible);
      })
    : [];
  const capabilities = workspace.lab.capabilities.filter(
    (cap) => cap.canonical_test_id === mappingTest,
  );

  async function saveBatch(supported: boolean) {
    if (!base || !data || !pendingBatch.length) return;
    const updated = await mutate(
      `/api/reports/${encodeURIComponent(data.report.document_id)}/scope`,
      {
        ...base,
        requirement_ids: pendingBatch,
        supported,
        note: batchNote.trim(),
        acknowledged: batchAck,
      },
      supported ? "Lab support review saved." : "Limitation saved.",
    );
    if (updated) setBatchOpen(false);
  }

  if (!documentId)
    return (
      <Box sx={{ maxWidth: 900, mx: "auto", py: 5 }}>
        <Typography variant="h4" fontWeight={700}>
          Reports
        </Typography>
        <Alert severity="info" sx={{ mt: 2 }}>
          Choose an analyzed specification to review lab coverage and create a
          draft report.
        </Alert>
        <Button component={RouterLink} to="/specifications" sx={{ mt: 1 }}>
          Open Specifications
        </Button>
      </Box>
    );
  if (loading)
    return (
      <Box sx={{ display: "grid", placeItems: "center", minHeight: 300 }}>
        <CircularProgress />
      </Box>
    );
  if (!data) {
    const needsAnalysis =
      error.toLowerCase().includes("analyze") || error.includes("400");
    return (
      <Box sx={{ maxWidth: 900, mx: "auto", py: 5 }}>
        <Typography variant="h4" fontWeight={700}>
          Reports
        </Typography>
        <Alert severity={needsAnalysis ? "info" : "error"} sx={{ mt: 2 }}>
          {needsAnalysis
            ? "Analyze this PDF in Specifications before creating a gap report."
            : error || "This report could not be loaded."}
        </Alert>
        <Button
          component={RouterLink}
          to={`/specifications?document=${encodeURIComponent(documentId)}`}
          sx={{ mt: 1 }}
        >
          Open Specifications
        </Button>
      </Box>
    );
  }

  return (
    <Box sx={{ maxWidth: 1440, mx: "auto", px: { xs: 1.5, md: 3 }, py: 3 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ xs: "flex-start", sm: "center" }}
        justifyContent="space-between"
        gap={1.5}
        sx={{ mb: 2.5 }}
      >
        <Box>
          <Typography variant="h4" fontWeight={750} letterSpacing="-.03em">
            Reports
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {data.report.original_name} · {workspace.lab.name} · Review and
            draft only
          </Typography>
        </Box>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          gap={1}
          sx={{ width: { xs: "100%", sm: "auto" } }}
        >
          <TextField
            select
            size="small"
            label="Customer specification"
            value={documentId}
            onChange={(event) => {
              setSelectedDocument(event.target.value);
              setSearchParams({ document: event.target.value });
            }}
            sx={{ minWidth: { sm: 260 } }}
          >
            {workspace.documents.map((item) => (
              <MenuItem key={item.document_id} value={item.document_id}>
                {item.original_name}
              </MenuItem>
            ))}
          </TextField>
          <Button
            component={RouterLink}
            to={`/specifications?document=${encodeURIComponent(data.report.document_id)}`}
            variant="outlined"
            size="small"
          >
            Review source specification
          </Button>
        </Stack>
      </Stack>
      {(error || notice) && (
        <Alert
          action={
            error ? (
              <Button
                color="inherit"
                size="small"
                onClick={() => window.location.reload()}
              >
                Discard changes & reload
              </Button>
            ) : undefined
          }
          severity={error ? "error" : "success"}
          onClose={() => {
            setError("");
            setNotice("");
          }}
          sx={{ mb: 2 }}
        >
          {error || notice}
        </Alert>
      )}
      {data.coverage_issues.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Some tests may be missing from extraction. Check the original PDF for
          omissions before sharing this report.
        </Alert>
      )}
      <Paper variant="outlined" sx={{ ...panelSx, mb: 2 }}>
        <Tabs
          value={tab}
          onChange={(_, value: TabName) => setTab(value)}
          variant="scrollable"
          allowScrollButtonsMobile
          sx={{ px: 1, "& .MuiTabs-indicator": { bgcolor: blue } }}
        >
          <Tab value="review" label="Review & confirm" />
          <Tab value="report" label="Report" />
          <Tab value="developer" label="Developer" />
        </Tabs>
      </Paper>

      {tab === "review" && (
        <>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.25}
            sx={{ mb: 1.5 }}
          >
            <SummaryCard
              label="Need review"
              count={pendingCount}
              tint="#f4f7ff"
            />
            <SummaryCard
              label="Operating support resolved"
              count={resolvedCount}
              tint="#f3faf7"
            />
            <SummaryCard
              label="Catalog / scope gaps"
              count={gapCount}
              tint="#fff8ed"
            />
          </Stack>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mb: 2 }}
          >
            Source text checks, catalog matching and lab scope checks can
            overlap. Missing catalog entries remain gaps; they do not prove
            impossibility.
          </Typography>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            flexWrap="wrap"
            gap={1}
            sx={{ mb: 1.5 }}
          >
            <TextField
              select
              size="small"
              label="Show"
              value={filter}
              onChange={(event) => setFilter(event.target.value as FilterName)}
              sx={{ minWidth: 190 }}
            >
              {(
                [
                  "Needs review",
                  "All active",
                  "Methods listed",
                  "Gaps",
                  "Excluded",
                ] as FilterName[]
              ).map((item) => (
                <MenuItem key={item} value={item}>
                  {item}
                </MenuItem>
              ))}
            </TextField>
            <Stack direction="row" gap={1}>
              <Button
                variant="outlined"
                disabled={
                  !lines.some(
                    (line) => data.line_states[idOf(line)]?.batch_eligible,
                  )
                }
                onClick={() => {
                  setBatchOpen(true);
                  setBatchIds([]);
                  setBatchAck(false);
                  setBatchDecision("");
                  setBatchNote("");
                }}
              >
                Batch lab review
              </Button>
              <Button variant="contained" onClick={() => setTab("report")}>
                Continue to report{" "}
                <ArrowForwardRoundedIcon sx={{ ml: 0.5, fontSize: 18 }} />
              </Button>
            </Stack>
          </Stack>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                md: "minmax(245px, .34fr) minmax(0, 1fr)",
              },
              gap: 1.5,
              alignItems: "start",
            }}
          >
            <Paper variant="outlined" sx={{ ...panelSx, p: 1.5 }}>
              <Typography
                variant="subtitle1"
                fontWeight={700}
                sx={{ px: 0.5, pb: 1 }}
              >
                Choose a test
              </Typography>
              <Stack spacing={0.6}>
                {visibleLines.map((line) => (
                  <Button
                    key={idOf(line)}
                    onClick={() => setSelectedId(idOf(line))}
                    variant={selectedId === idOf(line) ? "contained" : "text"}
                    color={selectedId === idOf(line) ? "primary" : "inherit"}
                    sx={{
                      justifyContent: "flex-start",
                      textAlign: "left",
                      alignItems: "flex-start",
                      p: 1.2,
                      borderRadius: 1.5,
                    }}
                  >
                    <Box sx={{ minWidth: 0, width: "100%" }}>
                      <Typography
                        variant="body2"
                        fontWeight={650}
                        sx={{ whiteSpace: "normal", overflowWrap: "anywhere" }}
                      >
                        {line.requirement.original_test_name}
                      </Typography>
                      <Typography variant="caption" sx={{ opacity: 0.8 }}>
                        {statusText(data, line)}
                      </Typography>
                    </Box>
                  </Button>
                ))}
                {!visibleLines.length && (
                  <Alert severity="info">
                    No tests in this group. Choose another group or continue to
                    your report.
                  </Alert>
                )}
              </Stack>
              <Divider sx={{ my: 1.3 }} />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={advanceAfterSave}
                    onChange={(event) =>
                      setAdvanceAfterSave(event.target.checked)
                    }
                    size="small"
                  />
                }
                label={
                  <Typography variant="caption">
                    Next pending item after saving
                  </Typography>
                }
              />
            </Paper>
            {currentLine ? (
              <Paper
                variant="outlined"
                sx={{ ...panelSx, p: { xs: 1.5, md: 2.5 } }}
              >
                <Stack
                  direction={{ xs: "column", sm: "row" }}
                  justifyContent="space-between"
                  alignItems={{ xs: "flex-start", sm: "center" }}
                  gap={1}
                >
                  <Box>
                    <Typography variant="h6" fontWeight={700}>
                      {currentLine.requirement.original_test_name}
                    </Typography>
                    <Stack
                      direction="row"
                      gap={0.75}
                      flexWrap="wrap"
                      sx={{ mt: 0.7 }}
                    >
                      <Chip
                        size="small"
                        label={statusText(data, currentLine)}
                        color={
                          currentLine.status.includes("gap")
                            ? "warning"
                            : "default"
                        }
                      />
                      <Chip
                        size="small"
                        variant="outlined"
                        label={`Source: ${formatLabel(currentLine.extraction_review_state)}`}
                      />
                    </Stack>
                  </Box>
                  <Button
                    size="small"
                    component={RouterLink}
                    to={`/specifications?document=${encodeURIComponent(data.report.document_id)}&requirement=${encodeURIComponent(idOf(currentLine))}`}
                    variant="outlined"
                  >
                    Check source text · p.{" "}
                    {currentLine.requirement.evidence
                      .map((item) => item.page)
                      .filter(
                        (value, index, values) =>
                          values.indexOf(value) === index,
                      )
                      .join(", ") || "—"}
                  </Button>
                </Stack>
                {currentLine.extraction_review_state === "needs_attention" && (
                  <Alert severity="warning" sx={{ mt: 1.5 }}>
                    Check extracted text against the PDF in Specifications. Lab
                    support review here will not clear that source check.
                  </Alert>
                )}
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                    gap: 1.2,
                    mt: 2,
                  }}
                >
                  <Paper variant="outlined" sx={{ ...panelSx, p: 1.5 }}>
                    <Typography variant="subtitle2" fontWeight={700}>
                      Customer asks for
                    </Typography>
                    <Typography variant="body2" sx={{ mt: 0.6 }}>
                      {currentLine.requirement.requested_methods
                        .map(
                          (method) =>
                            method.code +
                            (method.revision
                              ? ` · rev ${method.revision}`
                              : ""),
                        )
                        .join(", ") || "Not stated"}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Requested method
                    </Typography>
                  </Paper>
                  <Paper variant="outlined" sx={{ ...panelSx, p: 1.5 }}>
                    <Typography variant="subtitle2" fontWeight={700}>
                      Your lab lists
                    </Typography>
                    <Typography variant="body2" sx={{ mt: 0.6 }}>
                      {joinOr(
                        currentLine.lab_method_identifiers,
                        currentLine.status === "needs_review"
                          ? "Matching needs clarification"
                          : "No matching method listed",
                      )}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {currentLine.canonical_test_name ??
                        "Test identity needs confirmation"}
                    </Typography>
                  </Paper>
                </Box>
                <Paper variant="outlined" sx={{ ...panelSx, p: 1.5, mt: 1.2 }}>
                  <Typography
                    variant="subtitle1"
                    fontWeight={700}
                    sx={{ mb: 1 }}
                  >
                    Requirements to check
                  </Typography>
                  <ValueList
                    title="Conditions"
                    values={currentLine.requirement.conditions}
                  />
                  <ValueList
                    title="Acceptance criteria"
                    values={currentLine.requirement.acceptance_criteria}
                  />
                  {currentLine.requirement.applicability.length > 0 && (
                    <Box sx={{ mb: 1.2 }}>
                      <Typography variant="subtitle2" fontWeight={700}>
                        Applicability
                      </Typography>
                      {currentLine.requirement.applicability.map((value, i) => (
                        <Typography variant="body2" key={i} sx={{ mt: 0.4 }}>
                          {value}
                        </Typography>
                      ))}
                    </Box>
                  )}
                  {currentLine.requirement.special_instructions.length > 0 && (
                    <Box sx={{ mb: 1.2 }}>
                      <Typography variant="subtitle2" fontWeight={700}>
                        Special instructions
                      </Typography>
                      {currentLine.requirement.special_instructions.map(
                        (value, i) => (
                          <Typography variant="body2" key={i} sx={{ mt: 0.4 }}>
                            {value}
                          </Typography>
                        ),
                      )}
                    </Box>
                  )}
                  {currentLine.capability && (
                    <>
                      <Typography variant="subtitle2" fontWeight={700}>
                        Your lab’s recorded limits
                      </Typography>
                      <Typography variant="body2" sx={{ mt: 0.4 }}>
                        {currentLine.capability.declared_constraints ||
                          "No operating limits declared."}
                      </Typography>
                      {currentLine.capability.structured_limits.map((limit) => (
                        <Typography
                          variant="caption"
                          display="block"
                          key={limit.parameter}
                        >
                          {limit.parameter}:{" "}
                          {[
                            limit.minimum && `min ${limit.minimum}`,
                            limit.maximum && `max ${limit.maximum}`,
                            ...limit.values,
                            ...limit.supported_values,
                          ]
                            .filter(Boolean)
                            .join(" – ")}{" "}
                          {limit.unit ?? ""}
                        </Typography>
                      ))}
                    </>
                  )}
                  {currentLine.operation_checks.length > 0 && (
                    <Accordion
                      disableGutters
                      elevation={0}
                      sx={{ mt: 1, "&:before": { display: "none" } }}
                    >
                      <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                        <Typography variant="body2" fontWeight={650}>
                          Automatic operating comparison
                        </Typography>
                      </AccordionSummary>
                      <AccordionDetails>
                        {currentLine.operation_checks.map((item, i) => (
                          <Typography variant="body2" key={i} sx={{ mb: 0.5 }}>
                            {item}
                          </Typography>
                        ))}
                        <Typography variant="caption" color="text.secondary">
                          This checks declared operating limits, not whether the
                          material will pass.
                        </Typography>
                      </AccordionDetails>
                    </Accordion>
                  )}
                </Paper>
                {currentLine.status === "excluded" ? (
                  <Alert severity="info" sx={{ mt: 1.5 }}>
                    Excluded in Specifications. This item is not included in the
                    quote.
                  </Alert>
                ) : (
                  <>
                    {["test_gap", "method_gap"].includes(
                      currentLine.status,
                    ) && (
                      <Alert severity="info" sx={{ mt: 1.5 }}>
                        This item is already included as a catalog gap. No lab
                        support confirmation is needed. If your lab offers it,
                        update My Lab or correct the match below.
                      </Alert>
                    )}
                    {currentLine.status === "condition_gap" && (
                      <Alert severity="warning" sx={{ mt: 1.5 }}>
                        This request exceeds declared operating limits or has
                        been marked outside scope. The gap is already visible in
                        the report.
                      </Alert>
                    )}
                    {["in_house", "condition_gap"].includes(
                      currentLine.status,
                    ) &&
                      currentLine.capability &&
                      currentLine.operation_assessment !== "gap" && (
                        <Box
                          sx={{
                            position: "sticky",
                            bottom: 8,
                            zIndex: 2,
                            mt: 1.5,
                            p: 1.5,
                            bgcolor: "rgba(255,255,255,.97)",
                            border: "1px solid #dce3ee",
                            borderRadius: 2,
                            boxShadow: "0 4px 18px rgba(21,42,74,.08)",
                          }}
                        >
                          {["automatic", "human"].includes(
                            data.line_states[idOf(currentLine)]
                              ?.support_basis ?? "",
                          ) && (
                            <Alert severity="success" sx={{ mb: 1 }}>
                              {data.line_states[idOf(currentLine)]
                                ?.support_basis === "automatic"
                                ? "Operating conditions matched automatically."
                                : "Lab support is already confirmed."}{" "}
                              You can still record a limitation if needed.
                            </Alert>
                          )}
                          <TextField
                            size="small"
                            fullWidth
                            label="Review note (required for a limitation)"
                            value={scopeNote}
                            onChange={(event) =>
                              setScopeNote(event.target.value)
                            }
                            multiline
                            minRows={2}
                          />
                          <Stack
                            direction={{ xs: "column", sm: "row" }}
                            spacing={1}
                            sx={{ mt: 1 }}
                          >
                            {!["automatic", "human"].includes(
                              data.line_states[idOf(currentLine)]
                                ?.support_basis ?? "",
                            ) && (
                              <Button
                                variant="contained"
                                disabled={busy}
                                onClick={() =>
                                  void support(currentLine, true, scopeNote)
                                }
                              >
                                Confirm lab support
                              </Button>
                            )}
                            <Button
                              variant="outlined"
                              color="warning"
                              disabled={busy || !scopeNote.trim()}
                              onClick={() =>
                                void support(currentLine, false, scopeNote)
                              }
                            >
                              Cannot meet this scope
                            </Button>
                          </Stack>
                        </Box>
                      )}
                    <Accordion
                      expanded={mappingOpen}
                      onChange={(_, expanded) => setMappingOpen(expanded)}
                      sx={{
                        mt: 2,
                        border: "1px solid #dce3ee",
                        borderRadius: "8px !important",
                        boxShadow: "none",
                        "&:before": { display: "none" },
                      }}
                    >
                      <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                        <Box>
                          <Typography fontWeight={700}>
                            Change test or method / record a gap
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            Correct matching or clarify which source methods
                            apply.
                          </Typography>
                        </Box>
                      </AccordionSummary>
                      <AccordionDetails>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ mb: 1.5 }}
                        >
                          A missing lab entry is already reported as a gap.
                          Selecting a different method does not make it
                          equivalent to the customer’s method.
                        </Typography>
                        <Stack spacing={1.3}>
                          <TextField
                            select
                            label="Test in reference catalog"
                            value={mappingTest}
                            onChange={(event) => {
                              setMappingTest(event.target.value);
                              setMappingCapability("");
                            }}
                          >
                            <MenuItem value="">
                              Unresolved / not in catalog
                            </MenuItem>
                            {workspace.catalog.tests.map((test) => (
                              <MenuItem key={test.id} value={test.id}>
                                {test.name}
                              </MenuItem>
                            ))}
                          </TextField>
                          <TextField
                            select
                            SelectProps={{ multiple: true }}
                            label="Methods required for this request"
                            value={mappingMethods}
                            onChange={(event) =>
                              setMappingMethods(
                                typeof event.target.value === "string"
                                  ? event.target.value.split(",")
                                  : event.target.value,
                              )
                            }
                            helperText="All original references remain in the report. Explain any removed method."
                          >
                            {methodCodes(currentLine).map((code) => (
                              <MenuItem key={code} value={code}>
                                {code}
                              </MenuItem>
                            ))}
                          </TextField>
                          <TextField
                            select
                            label="Your lab’s matching capability"
                            value={mappingCapability}
                            onChange={(event) =>
                              setMappingCapability(event.target.value)
                            }
                          >
                            <MenuItem value="">
                              No specific declaration selected
                            </MenuItem>
                            {capabilities.map((cap) => (
                              <MenuItem key={cap.id} value={cap.id}>
                                {cap.method_ids
                                  .map(
                                    (id) =>
                                      workspace.catalog.methods.find(
                                        (method) => method.id === id,
                                      )?.identifier ?? id,
                                  )
                                  .join(", ") || "No method listed"}{" "}
                                ·{" "}
                                {cap.declared_constraints ||
                                  "No operating limits recorded"}
                              </MenuItem>
                            ))}
                          </TextField>
                          <TextField
                            select
                            label="Can your lab meet these conditions?"
                            value={mappingVerdict}
                            onChange={(event) =>
                              setMappingVerdict(
                                event.target
                                  .value as MappingChoice["capability_verdict"],
                              )
                            }
                          >
                            <MenuItem value="unreviewed">
                              Conditions and scope not assessed
                            </MenuItem>
                            <MenuItem value="supported">
                              Lab can meet the selected conditions and scope
                            </MenuItem>
                            <MenuItem value="unsupported">
                              Lab cannot meet the selected conditions or scope
                            </MenuItem>
                          </TextField>
                          <TextField
                            label="Review note"
                            value={mappingNote}
                            onChange={(event) =>
                              setMappingNote(event.target.value)
                            }
                            multiline
                            minRows={2}
                            helperText="Required when removing a method or recording a limitation."
                          />
                          <Stack direction="row" gap={1}>
                            <Button
                              variant="contained"
                              disabled={busy}
                              onClick={() => void saveMapping()}
                            >
                              Save matching review
                            </Button>
                            <Button
                              disabled={busy || !currentLine.mapping_choice}
                              onClick={() =>
                                void mutate(
                                  `/api/reports/${encodeURIComponent(data.report.document_id)}/mapping/reset`,
                                  {
                                    ...base,
                                    requirement_id: idOf(currentLine),
                                  },
                                  "Automatic matching restored.",
                                )
                              }
                            >
                              Use automatic matching
                            </Button>
                          </Stack>
                        </Stack>
                      </AccordionDetails>
                    </Accordion>
                    <Accordion
                      sx={{
                        mt: 1,
                        border: "1px solid #dce3ee",
                        borderRadius: "8px !important",
                        boxShadow: "none",
                        "&:before": { display: "none" },
                      }}
                    >
                      <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                        <Typography fontWeight={700}>
                          Optional quote quantity
                        </Typography>
                      </AccordionSummary>
                      <AccordionDetails>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ mb: 1 }}
                        >
                          Optional and manually selected. Confirm applicability,
                          overlapping records and the quantity’s pricing basis.
                          No job total is inferred.
                        </Typography>
                        {currentLine.capability?.price ? (
                          <Typography variant="body2" sx={{ mb: 1 }}>
                            Catalog reference rate:{" "}
                            {currentLine.capability.currency}{" "}
                            {currentLine.capability.price} /{" "}
                            {currentLine.capability.pricing_basis}
                          </Typography>
                        ) : (
                          <Alert severity="info" sx={{ mb: 1 }}>
                            No catalog rate for this selection. No amount can be
                            calculated.
                          </Alert>
                        )}
                        <Stack spacing={1}>
                          <TextField
                            label="Quantity in catalog pricing basis"
                            value={quantity}
                            onChange={(event) =>
                              setQuantity(event.target.value)
                            }
                            inputProps={{ inputMode: "decimal" }}
                          />
                          <TextField
                            label="Quantity derivation / applicable scope"
                            value={quantityNote}
                            onChange={(event) =>
                              setQuantityNote(event.target.value)
                            }
                            multiline
                            minRows={2}
                          />
                          <FormControlLabel
                            control={
                              <Checkbox
                                checked={quantityAck}
                                onChange={(event) =>
                                  setQuantityAck(event.target.checked)
                                }
                              />
                            }
                            label="I checked applicability, overlapping records and pricing basis"
                          />
                          <Stack direction="row" gap={1}>
                            <Button
                              variant="contained"
                              disabled={
                                busy ||
                                !quantity.trim() ||
                                !quantityAck ||
                                !quantityNote.trim() ||
                                !data.line_states[idOf(currentLine)]
                                  ?.quote_eligible
                              }
                              onClick={() =>
                                void mutate(
                                  `/api/reports/${encodeURIComponent(data.report.document_id)}/quantity`,
                                  {
                                    ...base,
                                    requirement_id: idOf(currentLine),
                                    quantity: quantity.trim() || null,
                                    scope_and_basis_confirmed: quantityAck,
                                    note: quantityNote.trim(),
                                  },
                                  quantity.trim()
                                    ? "Quote quantity saved."
                                    : "Quote quantity removed.",
                                )
                              }
                            >
                              Save quantity
                            </Button>
                            {data.report.workspace.quantities[
                              idOf(currentLine)
                            ] && (
                              <Button
                                color="error"
                                disabled={busy}
                                onClick={() =>
                                  void mutate(
                                    `/api/reports/${encodeURIComponent(data.report.document_id)}/quantity`,
                                    {
                                      ...base,
                                      requirement_id: idOf(currentLine),
                                      quantity: null,
                                      scope_and_basis_confirmed: quantityAck,
                                      note: quantityNote.trim(),
                                    },
                                    "Quote quantity removed.",
                                  )
                                }
                              >
                                Remove quantity
                              </Button>
                            )}
                          </Stack>
                        </Stack>
                      </AccordionDetails>
                    </Accordion>
                  </>
                )}
              </Paper>
            ) : (
              <Paper variant="outlined" sx={{ ...panelSx, p: 3 }}>
                <Alert severity="info">
                  No tests in this group. Choose another group or continue to
                  your report.
                </Alert>
              </Paper>
            )}
          </Box>
          <Dialog
            open={batchOpen}
            onClose={() => setBatchOpen(false)}
            fullWidth
            maxWidth="md"
          >
            <DialogTitle>Batch lab review</DialogTitle>
            <DialogContent>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mb: 1.5 }}
              >
                Select only tests whose displayed conditions, criteria and
                applicable scope you have checked. This confirms lab support,
                not extracted text or a quote.
              </Typography>
              {error && (
                <Alert
                  severity="error"
                  sx={{ mb: 2 }}
                  action={
                    <Button
                      color="inherit"
                      size="small"
                      onClick={() => window.location.reload()}
                    >
                      Discard changes & reload
                    </Button>
                  }
                >
                  {error}
                </Alert>
              )}
              <TextField
                select
                fullWidth
                SelectProps={{ multiple: true }}
                label="Eligible tests"
                value={batchIds}
                onChange={(event) => {
                  setBatchIds(
                    typeof event.target.value === "string"
                      ? event.target.value.split(",")
                      : event.target.value,
                  );
                  setBatchAck(false);
                  setBatchDecision("");
                }}
                helperText={`${pendingBatch.length} selected · none selected automatically`}
                sx={{ mb: 1.5 }}
              >
                {lines
                  .filter(
                    (line) => data.line_states[idOf(line)]?.batch_eligible,
                  )
                  .map((line) => (
                    <MenuItem value={idOf(line)} key={idOf(line)}>
                      {line.requirement.original_test_name} ·{" "}
                      {methodCodes(line).join(", ") || "Method not stated"}
                    </MenuItem>
                  ))}
              </TextField>
              {pendingBatch.map((id) => {
                const line = lines.find((item) => idOf(item) === id)!;
                return (
                  <Paper
                    key={id}
                    variant="outlined"
                    sx={{ ...panelSx, p: 1.25, mb: 1 }}
                  >
                    <Typography variant="subtitle2" fontWeight={700}>
                      {line.requirement.original_test_name}
                    </Typography>
                    <Typography variant="caption" display="block">
                      Conditions:{" "}
                      {joinOr(
                        line.requirement.conditions.map(
                          (value) => `${value.name}: ${value.raw_value}`,
                        ),
                        "Not stated",
                      )}
                    </Typography>
                    <Typography variant="caption" display="block">
                      Criteria:{" "}
                      {joinOr(
                        line.requirement.acceptance_criteria.map(
                          (value) => `${value.name}: ${value.raw_value}`,
                        ),
                        "Not stated",
                      )}{" "}
                      · Scope: {joinOr(line.requirement.applicability)}
                    </Typography>
                    <Typography variant="caption" display="block">
                      Lab limits:{" "}
                      {line.capability?.declared_constraints || "Not declared"}
                    </Typography>
                  </Paper>
                );
              })}
              <RadioGroup
                row
                value={batchDecision}
                onChange={(event) => {
                  setBatchDecision(event.target.value);
                  setBatchAck(false);
                }}
              >
                <FormControlLabel
                  value="supported"
                  control={<Radio />}
                  label="Confirm selected"
                  disabled={pendingBatch.some(
                    (id) =>
                      lines.find((line) => idOf(line) === id)
                        ?.operation_assessment === "gap",
                  )}
                />
                <FormControlLabel
                  value="unsupported"
                  control={<Radio />}
                  label="Cannot meet selected"
                />
              </RadioGroup>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={batchAck}
                    disabled={!batchDecision || !pendingBatch.length}
                    onChange={(event) => setBatchAck(event.target.checked)}
                  />
                }
                label="I checked the conditions, criteria and applicable scope for every selected test"
              />
              <TextField
                fullWidth
                label="Batch review note / limitation"
                value={batchNote}
                onChange={(event) => setBatchNote(event.target.value)}
                multiline
                minRows={2}
                sx={{ mt: 1 }}
              />
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setBatchOpen(false)}>Cancel</Button>
              <Button
                variant="contained"
                disabled={
                  busy ||
                  !batchAck ||
                  !pendingBatch.length ||
                  !batchDecision ||
                  (batchDecision === "unsupported" && !batchNote.trim())
                }
                onClick={() => void saveBatch(batchDecision === "supported")}
              >
                Save batch review
              </Button>
            </DialogActions>
          </Dialog>
        </>
      )}

      {tab === "report" && (
        <Stack spacing={2}>
          <Paper variant="outlined" sx={{ ...panelSx, p: { xs: 2, md: 3 } }}>
            <Typography variant="h6" fontWeight={700}>
              Create your draft report
            </Typography>
            {pendingCount > 0 ? (
              <Alert severity="info" sx={{ my: 1.5 }}>
                {pendingCount} items still need review. You can create a draft
                now; unresolved items will be clearly marked.
              </Alert>
            ) : (
              <Alert severity="success" sx={{ my: 1.5 }}>
                No open item reviews. Recorded gaps will remain in the report.
              </Alert>
            )}
            <Typography variant="body2" color="text.secondary">
              Includes requested tests, conditions, lab coverage and gaps. This
              draft is not a final approved quote.
            </Typography>
            {Object.keys(data.report.totals_by_currency).length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Typography fontWeight={700}>
                  Partial quote subtotal · manually selected lines only
                </Typography>
                {Object.entries(data.report.totals_by_currency).map(
                  ([currency, value]) => (
                    <Typography key={currency}>
                      {currency} {value}
                    </Typography>
                  ),
                )}
              </Box>
            )}
            <Accordion sx={{ mt: 1.5, boxShadow: "none" }}>
              <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                Pricing included in this draft
              </AccordionSummary>
              <AccordionDetails>
                {lines
                  .filter((line) => line.quote)
                  .map((line) => (
                    <Typography key={idOf(line)} variant="body2">
                      {line.requirement.original_test_name}:{" "}
                      {line.quote?.quantity} × {line.quote?.currency}{" "}
                      {line.quote?.unit_price} / {line.quote?.pricing_basis} ={" "}
                      {line.quote?.currency} {line.quote?.subtotal}
                    </Typography>
                  ))}
                {!lines.some((line) => line.quote) && (
                  <Typography variant="body2">
                    No quote quantities confirmed. No job total is estimated.
                  </Typography>
                )}
                <Typography variant="caption" color="text.secondary">
                  Records may overlap and are not a billable test count. Unknown
                  rates, partner costs and lead times are not estimated.
                </Typography>
              </AccordionDetails>
            </Accordion>
            <Button
              variant="contained"
              startIcon={<DownloadRoundedIcon />}
              disabled={busy}
              onClick={() => void createSnapshot()}
              sx={{ mt: 2, bgcolor: blue, alignSelf: "flex-start" }}
            >
              {busy ? "Preparing…" : "Create draft report"}
            </Button>
          </Paper>
          <Paper variant="outlined" sx={{ ...panelSx, p: { xs: 2, md: 3 } }}>
            <Typography variant="h6" fontWeight={700}>
              Preview & download
            </Typography>
            {!data.saved_reports.length ? (
              <Alert severity="info" sx={{ mt: 1.5 }}>
                No report saved for this specification. Create a draft report
                above to preview it.
              </Alert>
            ) : (
              <Stack spacing={1.5} sx={{ mt: 1.5 }}>
                <TextField
                  select
                  label="Saved report"
                  value={snapshotId || data.saved_reports[0].report_id}
                  onChange={(event) => void loadSnapshot(event.target.value)}
                >
                  {data.saved_reports.map((item) => (
                    <MenuItem key={item.report_id} value={item.report_id}>
                      {new Date(item.created_at).toLocaleString()} ·{" "}
                      {item.report_id.slice(0, 8)} ·{" "}
                      {item.current ? "Current inputs" : "Historical snapshot"}
                    </MenuItem>
                  ))}
                </TextField>
                {!currentSnapshot && (
                  <Alert severity="warning">
                    Saved snapshot: source, lab or review data has changed.
                    Generate a new report for the current state.
                  </Alert>
                )}
                <Typography variant="caption" color="text.secondary">
                  Draft for engineer review · not an approved quote or proof of
                  capability.
                </Typography>
                {snapshotLoading && <CircularProgress size={24} />}
                {snapshot && (
                  <>
                    <Stack direction="row" gap={1} flexWrap="wrap">
                      <Button
                        component="a"
                        href={`/api/snapshots/${encodeURIComponent(snapshotId)}/pdf?download=true`}
                        download
                        variant="contained"
                      >
                        Download PDF
                      </Button>
                      <Button
                        component="a"
                        href={`/api/snapshots/${encodeURIComponent(snapshotId)}/json`}
                        download
                        variant="outlined"
                      >
                        Download JSON
                      </Button>
                      <TextField
                        select
                        size="small"
                        label="Report page"
                        value={snapshotPage}
                        onChange={(event) =>
                          setSnapshotPage(Number(event.target.value))
                        }
                      >
                        {Array.from({ length: snapshot.page_count }, (_, i) => (
                          <MenuItem key={i + 1} value={i + 1}>
                            {i + 1} of {snapshot.page_count}
                          </MenuItem>
                        ))}
                      </TextField>
                    </Stack>
                    <Box
                      component="img"
                      src={`/api/snapshots/${encodeURIComponent(snapshotId)}/pages/${snapshotPage}`}
                      alt={`Report preview page ${snapshotPage}`}
                      sx={{
                        display: "block",
                        maxWidth: "100%",
                        maxHeight: "75vh",
                        objectFit: "contain",
                        mx: "auto",
                        border: "1px solid #dce3ee",
                        borderRadius: 1,
                      }}
                    />
                  </>
                )}
              </Stack>
            )}
          </Paper>
        </Stack>
      )}

      {tab === "developer" && (
        <Stack spacing={2}>
          <Paper variant="outlined" sx={{ ...panelSx, p: 2 }}>
            <Typography variant="h6" fontWeight={700}>
              Matching & review diagnostics
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ my: 1 }}>
              Deterministic catalog matching and operating-limit comparison.
              This is not LLM confidence or standard-content comparison.
            </Typography>
            <Box sx={{ overflowX: "auto" }}>
              <Box
                component="table"
                sx={{
                  width: "100%",
                  borderCollapse: "collapse",
                  "& th, & td": {
                    textAlign: "left",
                    p: 1,
                    borderBottom: "1px solid #e5eaf1",
                    fontSize: 13,
                  },
                }}
              >
                <thead>
                  <tr>
                    {[
                      "Customer test",
                      "Result",
                      "Content review",
                      "Lab scope review",
                      "Requested methods",
                      "Lab methods",
                    ].map((value) => (
                      <th key={value}>{value}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {lines.map((line) => (
                    <tr key={idOf(line)}>
                      <td>{line.requirement.original_test_name}</td>
                      <td>{formatLabel(line.status)}</td>
                      <td>{formatLabel(line.extraction_review_state)}</td>
                      <td>
                        {data.line_states[idOf(line)]?.scope_state ?? "Unknown"}
                      </td>
                      <td>{methodCodes(line).join(", ")}</td>
                      <td>{line.lab_method_identifiers.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </Box>
            </Box>
          </Paper>
          <Paper variant="outlined" sx={{ ...panelSx, p: 2 }}>
            <Typography variant="h6" fontWeight={700}>
              Input versions & warnings
            </Typography>
            <Box
              component="pre"
              sx={{
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                bgcolor: "#f5f7fb",
                p: 1.5,
                borderRadius: 1,
              }}
            >
              {JSON.stringify(
                {
                  fingerprint: data.report.input_fingerprint,
                  run_id: data.report.source_run.run_id,
                  workspace_version: data.report.workspace.version,
                  lab_version: data.report.lab.version,
                  catalog_version: data.report.catalog.version,
                  warnings: data.report.warnings,
                  coverage_issues: data.coverage_issues,
                },
                null,
                2,
              )}
            </Box>
            <Typography variant="subtitle2" fontWeight={700}>
              Line decisions / source fields
            </Typography>
            <Box
              component="pre"
              sx={{
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                overflow: "auto",
                maxHeight: 480,
                bgcolor: "#f5f7fb",
                p: 1.5,
                borderRadius: 1,
              }}
            >
              {JSON.stringify(lines, null, 2)}
            </Box>
            {snapshot && (
              <>
                <Typography variant="subtitle2" fontWeight={700}>
                  Selected saved report data
                </Typography>
                <Box
                  component="pre"
                  sx={{
                    whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere",
                    maxHeight: 380,
                    overflow: "auto",
                    bgcolor: "#f5f7fb",
                    p: 1.5,
                    borderRadius: 1,
                  }}
                >
                  {JSON.stringify(snapshot.report, null, 2)}
                </Box>
              </>
            )}
          </Paper>
        </Stack>
      )}
    </Box>
  );
}
