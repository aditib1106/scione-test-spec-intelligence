import { useEffect, useMemo, useRef, useState } from "react";
import {
  Link as RouterLink,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  List,
  ListItemButton,
  Menu,
  MenuItem,
  Paper,
  Select,
  Snackbar,
  Stack,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import DownloadRoundedIcon from "@mui/icons-material/DownloadRounded";
import OpenInNewRoundedIcon from "@mui/icons-material/OpenInNewRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import MoreHorizRoundedIcon from "@mui/icons-material/MoreHorizRounded";
import { apiFetch, useResource } from "../api";
import { useAuth } from "../auth";
import PdfPageViewer from "../components/PdfPageViewer";
import ReportGenerationControls from "../components/ReportGenerationControls";
import ReportDimensions, {
  type ReportDimension,
} from "../components/ReportDimensions";
import ReportScopeControls from "../components/ReportScopeControls";
import ReportAssessmentReview from "../components/ReportAssessmentReview";
import DocumentRevisionNotice from "../components/DocumentRevisionNotice";
import SourceCoverageNotice from "../components/SourceCoverageNotice";
import type { PageProps } from "../types";
import type { LibraryDocument } from "../libraryTypes";
import type {
  ComparisonData,
  ComparisonRequirement,
  ComparisonStatus,
} from "../comparisonTypes";
import {
  readReportGenerationIntent,
  type ReportGenerationIntent,
} from "../reportGeneration";

const results: Record<
  ComparisonStatus,
  {
    label: string;
    color: "success" | "warning" | "error" | "default";
    tone: string;
  }
> = {
  fully_matched: { label: "Fully matched", color: "success", tone: "#227451" },
  partially_matched: {
    label: "Partially matched",
    color: "warning",
    tone: "#946018",
  },
  not_matched: { label: "Not matched", color: "error", tone: "#ba3945" },
  needs_information: {
    label: "Needs information",
    color: "default",
    tone: "#315c9f",
  },
  excluded: { label: "Excluded", color: "default", tone: "#66758b" },
};
const filters = [
  { value: "all", label: "All tests" },
  { value: "attention", label: "Needs attention" },
  { value: "pending_review", label: "Not reviewed" },
  { value: "reviewed", label: "Reviewed" },
  ...Object.entries(results).map(([value, status]) => ({
    value,
    label: status.label,
  })),
];
const priority: Record<ComparisonStatus, number> = {
  not_matched: 0,
  partially_matched: 1,
  needs_information: 2,
  fully_matched: 3,
  excluded: 4,
};
interface ReportView {
  selectedId: string;
  filter: string;
  query: string;
  order: string;
}
const emptyView: ReportView = {
  selectedId: "",
  filter: "all",
  query: "",
  order: "priority",
};
const viewKey = "scione.report.views";
function readViews(): Record<string, ReportView> {
  try {
    const saved = JSON.parse(sessionStorage.getItem(viewKey) || "{}");
    return saved && typeof saved === "object" && !Array.isArray(saved)
      ? saved
      : {};
  } catch {
    return {};
  }
}
function selectRequirements(report: ComparisonData | null, view: ReportView) {
  const needle = view.query.trim().toLocaleLowerCase();
  const items = (report?.requirements ?? []).filter(
    (item) =>
      (view.filter === "all" ||
        (view.filter === "pending_review"
          ? item.comparison.status !== "excluded" &&
            report?.assessment_reviews?.[item.requirement_id]?.review_status !==
              "accepted"
          : view.filter === "reviewed"
            ? report?.assessment_reviews?.[item.requirement_id]
                ?.review_status === "accepted"
            : view.filter === "attention"
              ? [
                  "partially_matched",
                  "not_matched",
                  "needs_information",
                ].includes(item.comparison.status)
              : item.comparison.status === view.filter)) &&
      [item.test_name, ...item.methods]
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle),
  );
  return view.order === "source"
    ? items
    : [...items].sort(
        (a, b) => priority[a.comparison.status] - priority[b.comparison.status],
      );
}
function resultLabel(status: ComparisonStatus) {
  return results[status] ?? results.needs_information;
}
function statusStyle(status: ComparisonStatus) {
  const background = {
    fully_matched: "#e6f3eb",
    partially_matched: "#fff0d6",
    not_matched: "#fde8eb",
    needs_information: "#eaf0f6",
    excluded: "#edf0f3",
  };
  return {
    bgcolor: background[status],
    color: resultLabel(status).tone,
    fontWeight: 700,
    height: 27,
    fontSize: 13,
    borderRadius: 1,
    "& .MuiChip-label": { px: 1 },
  };
}
function sourceUrl(documentId: string, requirementId: string) {
  return `/specifications?${new URLSearchParams({ document: documentId, requirement: requirementId })}`;
}
function nextActions(documentId: string, requirement: ComparisonRequirement) {
  const unknown = requirement.comparison.checks.filter(
    (check) => check.status === "unknown",
  );
  const sourceQuestion =
    requirement.source_review_state === "needs_attention" ||
    requirement.source_review_state === "needs_review" ||
    unknown.some(
      (check) =>
        check.kind === "source" ||
        check.parameter_id === "source_review" ||
        !check.parameter_id ||
        check.requirement_claim?.normalization_state === "unresolved",
    );
  const methodQuestion = unknown.some(
    (check) => check.kind === "method" || check.parameter_id === "test_method",
  );
  const actions: { label: string; to: string; description: string }[] = [];
  if (sourceQuestion)
    actions.push({
      label: "Review extracted details",
      to: sourceUrl(documentId, requirement.requirement_id),
      description:
        "Check the source text and any unclear requirements before reassessing this test.",
    });
  if (methodQuestion && !sourceQuestion)
    actions.push({
      label: "Review test and method",
      to: sourceUrl(documentId, requirement.requirement_id),
      description:
        "Check the requested test and method against the original source.",
    });
  const methodGap = requirement.comparison.checks.some(
    (check) =>
      check.status === "gap" &&
      (check.kind === "method" || check.parameter_id === "test_method"),
  );
  if (!requirement.capability_id && methodGap)
    actions.push({
      label: "Add a lab capability",
      to: "/my-lab",
      description:
        "This method is not listed. Add a declaration if your lab can offer it.",
    });
  if (unknown.length && requirement.capability_id)
    actions.push({
      label: "Update lab information",
      to: `/my-lab?${new URLSearchParams({ capability: requirement.capability_id })}`,
      description:
        "Add the missing lab details, then recompare without analyzing the customer PDF again.",
    });
  return actions;
}

export default function Comparison({ workspace }: PageProps) {
  const { canDevelop } = useAuth();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const docs = workspace.documents.filter((document) => document.latest_run_id);
  const documentId = params.get("document") || docs[0]?.document_id || "";
  const [generationIntent, setGenerationIntent] =
    useState<ReportGenerationIntent | null>(null);
  useEffect(() => {
    const intent = readReportGenerationIntent(location.state, documentId);
    if (!intent) return;
    setGenerationIntent(intent);
    const nextState = { ...location.state };
    delete nextState.generateReportFor;
    delete nextState.generationIntentId;
    // Consume the navigation action before starting inference. Browser refresh,
    // ordinary report visits and Back must never repeat a paid operation.
    navigate(
      {
        pathname: location.pathname,
        search: location.search,
        hash: location.hash,
      },
      {
        replace: true,
        state: Object.keys(nextState).length ? nextState : null,
        preventScrollReset: true,
      },
    );
  }, [
    documentId,
    location.state,
    location.pathname,
    location.search,
    location.hash,
    navigate,
  ]);
  const data = useResource<ComparisonData>(
    documentId ? `/api/comparisons/${encodeURIComponent(documentId)}` : null,
  );
  const library = useResource<{ documents: LibraryDocument[] }>("/api/library");
  const [views, setViews] = useState(readViews);
  const saved = views[documentId];
  const view: ReportView = {
    selectedId: typeof saved?.selectedId === "string" ? saved.selectedId : "",
    filter: filters.some((item) => item.value === saved?.filter)
      ? saved.filter
      : "all",
    query: typeof saved?.query === "string" ? saved.query : "",
    order: saved?.order === "source" ? "source" : "priority",
  };
  const updateView = (changes: Partial<ReportView>) =>
    setViews((current) => ({
      ...current,
      [documentId]: { ...emptyView, ...current[documentId], ...changes },
    }));
  useEffect(() => {
    try {
      sessionStorage.setItem(viewKey, JSON.stringify(views));
    } catch {
      /* Preferences are optional. */
    }
  }, [views]);
  const report =
    data.data?.document.document_id === documentId ? data.data : null;
  const visible = useMemo(
    () => selectRequirements(report, view),
    [report, view.filter, view.query, view.order],
  );
  const selected =
    visible.find((item) => item.requirement_id === view.selectedId) ||
    visible[0];
  // Commit the visible selection. A previously hidden ID must not reappear when
  // a filter is cleared or a saved comparison is refreshed.
  useEffect(() => {
    if (selected && selected.requirement_id !== view.selectedId)
      setViews((current) => ({
        ...current,
        [documentId]: {
          ...emptyView,
          ...current[documentId],
          selectedId: selected.requirement_id,
        },
      }));
  }, [documentId, selected?.requirement_id, view.selectedId]);
  const changeView = (changes: Partial<ReportView>) => {
    const next = { ...view, ...changes };
    const available = selectRequirements(report, next);
    const selectedId = available.some(
      (item) => item.requirement_id === next.selectedId,
    )
      ? next.selectedId
      : available[0]?.requirement_id || next.selectedId;
    updateView({ ...changes, selectedId });
  };
  const [expanded, setExpanded] = useState<
    Record<string, Partial<Record<ReportDimension, boolean>>>
  >({});
  const selectedKey = `${documentId}:${selected?.requirement_id ?? ""}`;
  const [tab, setTab] = useState("comparison");
  const [notice, setNotice] = useState("");
  const [toolsAnchor, setToolsAnchor] = useState<HTMLElement | null>(null);
  const detailPane = useRef<HTMLDivElement>(null);
  const previousWorkspace = useRef({
    labVersion: workspace.lab.version,
    documents: workspace.documents,
  });
  useEffect(() => {
    const previous = previousWorkspace.current;
    if (
      previous.labVersion === workspace.lab.version &&
      previous.documents === workspace.documents
    )
      return;
    previousWorkspace.current = {
      labVersion: workspace.lab.version,
      documents: workspace.documents,
    };
    data.refresh();
  }, [workspace.lab.version, workspace.documents, data.refresh]);

  const [page, setPage] = useState(1);
  const [pageImage, setPageImage] = useState("");
  const [pageLoading, setPageLoading] = useState(false);
  const [pageError, setPageError] = useState("");
  useEffect(() => {
    setPage(1);
    setNotice("");
  }, [documentId]);
  useEffect(() => {
    setPage(1);
  }, [report?.comparison_id]);
  const reportBase = report
    ? `/api/comparison-snapshots/${encodeURIComponent(report.comparison_id)}`
    : "";
  const snapshot = useResource<{ page_count: number }>(
    tab === "report" && reportBase ? reportBase : null,
  );
  const pages = Math.max(
    1,
    snapshot.data?.page_count ?? report?.pdf_page_count ?? 1,
  );
  useEffect(() => {
    if (tab !== "report" || !reportBase) return;
    const controller = new AbortController();
    let objectUrl = "";
    setPageLoading(true);
    setPageError("");
    setPageImage("");
    apiFetch(`${reportBase}/pages/${page}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok)
          throw new Error(
            "The report preview could not be opened. You can retry or download the PDF.",
          );
        return response.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (!controller.signal.aborted) setPageImage(objectUrl);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setPageError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setPageLoading(false);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [tab, reportBase, page]);
  const actions = selected ? nextActions(documentId, selected) : [];
  const counts = Object.fromEntries(
    filters.map(({ value }) => [
      value,
      selectRequirements(report, { ...emptyView, filter: value }).length,
    ]),
  );
  const docTitle = (id: string, fallback: string) => {
    const document = library.data?.documents.find(
      (item) => item.document_id === id,
    );
    return document
      ? [
          document.company_name,
          document.specification_id || document.title || fallback,
          document.revision,
        ]
          .filter(Boolean)
          .join(" · ")
      : fallback;
  };
  const scopeSaved = (updated: ComparisonData) => {
    const oldIndex = visible.findIndex(
      (item) => item.requirement_id === selected?.requirement_id,
    );
    const next = selectRequirements(updated, view);
    const stayed = next.some(
      (item) => item.requirement_id === selected?.requirement_id,
    );
    updateView({
      selectedId: stayed
        ? selected!.requirement_id
        : next[Math.min(Math.max(oldIndex, 0), next.length - 1)]
            ?.requirement_id || "",
    });
    data.setData(updated);
    setNotice(
      selected?.report_scope_exclusion
        ? "Test restored to this report."
        : "Test removed from this report. You can restore it here.",
    );
  };

  return (
    <Box
      sx={{
        px: { xs: 2, md: 2.5 },
        py: 1.5,
        maxWidth: 1800,
        mx: "auto",
        height: {
          md: "calc(100dvh - var(--workspace-header-height, 64px))",
        },
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
      }}
    >
      <Stack
        direction={{ xs: "column", lg: "row" }}
        justifyContent="space-between"
        alignItems={{ lg: "center" }}
        spacing={1.5}
        sx={{ mb: 0.75, flexShrink: 0, minWidth: 0 }}
      >
        <Typography
          variant="h5"
          sx={{ fontSize: 25, lineHeight: 1.25, whiteSpace: "nowrap" }}
        >
          Gap report
        </Typography>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          useFlexGap
          flexWrap={{ xs: "wrap", sm: "nowrap" }}
          sx={{ minWidth: 0, flex: 1, justifyContent: { lg: "flex-end" } }}
        >
          {docs.length > 0 && (
            <FormControl
              size="small"
              sx={{
                flex: { xs: "1 1 100%", sm: "1 1 240px", lg: "0 1 360px" },
                minWidth: { sm: 220 },
                maxWidth: { lg: 360 },
              }}
            >
              <InputLabel id="comparison-document-label">
                Customer specification
              </InputLabel>
              <Select
                labelId="comparison-document-label"
                label="Customer specification"
                value={
                  docs.some((item) => item.document_id === documentId)
                    ? documentId
                    : ""
                }
                onChange={(event) =>
                  setParams({ document: event.target.value })
                }
                sx={{ bgcolor: "background.paper", fontSize: 14 }}
              >
                {docs.map((document) => (
                  <MenuItem
                    key={document.document_id}
                    value={document.document_id}
                  >
                    {docTitle(document.document_id, document.original_name)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
          {report && (
            <ReportGenerationControls
              key={documentId}
              documentId={documentId}
              comparisonId={report.comparison_id}
              aiApplied={report.comparison_engine === "ai_semantic_matching"}
              providers={workspace.providers}
              requirementCount={report.requirements.length}
              generationIntentId={
                generationIntent?.generateReportFor === documentId
                  ? generationIntent.generationIntentId
                  : null
              }
              onComplete={data.refresh}
            />
          )}
          <Stack direction="row" spacing={0.25} sx={{ flexShrink: 0 }}>
            <Tooltip title="Recompare saved information">
              <span>
                <IconButton
                  aria-label="Recompare saved information"
                  disabled={data.loading || !documentId}
                  onClick={data.refresh}
                  size="small"
                >
                  {data.loading ? (
                    <CircularProgress size={18} />
                  ) : (
                    <RefreshRoundedIcon fontSize="small" />
                  )}
                </IconButton>
              </span>
            </Tooltip>
            {documentId && canDevelop && (
              <IconButton
                aria-label="Report options"
                onClick={(event) => setToolsAnchor(event.currentTarget)}
                size="small"
              >
                <MoreHorizRoundedIcon />
              </IconButton>
            )}
          </Stack>
        </Stack>
      </Stack>
      {report && report.comparison_engine !== "ai_semantic_matching" && (
        <Typography variant="caption" color="text.secondary" sx={{ mb: 0.75 }}>
          Generate a report to compare these requirements with your saved lab
          information using AI.
        </Typography>
      )}
      {report && (
        <DocumentRevisionNotice
          issues={report.document.document_review_issues}
          sourceReviewUrl={`/specifications?${new URLSearchParams({ document: documentId })}`}
        />
      )}
      {report && (
        <SourceCoverageNotice
          key={`coverage-${documentId}`}
          issues={report.source_coverage_issues ?? []}
          sourceReviewUrl={`/specifications?${new URLSearchParams({ document: documentId })}`}
        />
      )}
      {canDevelop && (
        <Menu
          anchorEl={toolsAnchor}
          open={Boolean(toolsAnchor)}
          onClose={() => setToolsAnchor(null)}
        >
          <MenuItem
            component={RouterLink}
            to={`/reports/advanced?${new URLSearchParams({ document: documentId })}`}
            onClick={() => setToolsAnchor(null)}
          >
            Review test matching
          </MenuItem>
        </Menu>
      )}
      {!documentId ? (
        <Paper variant="outlined" sx={{ p: 5, textAlign: "center" }}>
          <Typography variant="h6">
            Start with a customer specification
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 1, mb: 2 }}>
            Save and extract a specification to compare its requirements with
            your lab.
          </Typography>
          <Button
            component={RouterLink}
            to="/specifications/library"
            variant="contained"
            endIcon={<ArrowForwardRoundedIcon />}
          >
            Open specification library
          </Button>
        </Paper>
      ) : (
        <>
          {data.error && (
            <Alert
              severity="error"
              action={<Button onClick={data.refresh}>Retry</Button>}
              sx={{ mb: 1.5 }}
            >
              {data.error}
            </Alert>
          )}
          {data.loading && !report && (
            <Box sx={{ py: 8, textAlign: "center" }}>
              <CircularProgress size={28} />
              <Typography color="text.secondary" sx={{ mt: 1.5 }}>
                Comparing saved requirements with your lab…
              </Typography>
            </Box>
          )}
          {report && (
            <>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                alignItems={{ sm: "center" }}
                justifyContent="space-between"
                sx={{ mb: 1, flexShrink: 0, minHeight: 34 }}
              >
                <Tabs
                  value={tab}
                  onChange={(_, value: string) => setTab(value)}
                  aria-label="Gap report views"
                  sx={{
                    minHeight: 34,
                    "& .MuiTab-root": {
                      minHeight: 34,
                      px: 1.25,
                      py: 0.5,
                      fontSize: 14,
                    },
                  }}
                >
                  <Tab label="Comparison" value="comparison" />
                  <Tab label="PDF & download" value="report" />
                </Tabs>
                <Stack
                  direction="row"
                  spacing={{ xs: 1.5, md: 2.25 }}
                  alignItems="center"
                  aria-label="Report summary"
                  sx={{ overflowX: "auto", minWidth: 0, pb: 0.25 }}
                >
                  {(
                    [
                      ["fully_matched", "Matched"],
                      ["partially_matched", "Partial"],
                      ["not_matched", "Gap"],
                      ["needs_information", "Needs information"],
                      ...(counts.excluded ? [["excluded", "Excluded"]] : []),
                    ] as [ComparisonStatus, string][]
                  ).map(([status, label]) => (
                    <Stack
                      key={status}
                      direction="row"
                      spacing={0.65}
                      alignItems="baseline"
                      sx={{ flexShrink: 0 }}
                    >
                      <Typography
                        component="span"
                        sx={{
                          fontSize: 20,
                          fontWeight: 750,
                          lineHeight: 1,
                          color: resultLabel(status).tone,
                        }}
                      >
                        {counts[status] ?? 0}
                      </Typography>
                      <Typography
                        component="span"
                        sx={{ fontSize: 13, color: "text.secondary" }}
                      >
                        {label}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              </Stack>
              {tab === "comparison" && (
                <>
                  <Box
                    sx={{
                      display: "grid",
                      gridTemplateColumns: {
                        xs: "minmax(0,1fr)",
                        md: "220px minmax(0,1fr)",
                      },
                      gap: { xs: 1.5, md: 1.5 },
                      flex: 1,
                      minHeight: 0,
                    }}
                  >
                    <Paper
                      variant="outlined"
                      component="nav"
                      aria-label="Requested tests"
                      sx={{
                        overflow: "hidden",
                        display: "flex",
                        flexDirection: "column",
                        minHeight: 0,
                        maxHeight: { xs: 350, md: "none" },
                      }}
                    >
                      <Box
                        sx={{
                          p: 1.5,
                          borderBottom: "1px solid",
                          borderColor: "divider",
                          flexShrink: 0,
                        }}
                      >
                        <Stack
                          direction="row"
                          justifyContent="space-between"
                          alignItems="baseline"
                          sx={{ mb: 1.25 }}
                        >
                          <Typography
                            variant="subtitle1"
                            sx={{ fontSize: 15, fontWeight: 700 }}
                          >
                            Test items
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {visible.length} of {report.requirements.length}
                          </Typography>
                        </Stack>
                        <TextField
                          fullWidth
                          size="small"
                          placeholder="Search tests"
                          value={view.query}
                          onChange={(event) =>
                            changeView({ query: event.target.value })
                          }
                          inputProps={{
                            "aria-label": "Search requested tests",
                            "data-report-focus": true,
                          }}
                          InputProps={{
                            startAdornment: (
                              <InputAdornment position="start">
                                <SearchRoundedIcon fontSize="small" />
                              </InputAdornment>
                            ),
                          }}
                        />
                        <Stack direction="row" spacing={0.75} sx={{ mt: 1.25 }}>
                          <FormControl
                            size="small"
                            sx={{ flex: 1, minWidth: 0 }}
                          >
                            <InputLabel id="comparison-result-label">
                              Show
                            </InputLabel>
                            <Select
                              labelId="comparison-result-label"
                              label="Show"
                              value={view.filter}
                              onChange={(event) =>
                                changeView({ filter: event.target.value })
                              }
                            >
                              {filters.map(({ value, label }) => (
                                <MenuItem key={value} value={value}>
                                  {label}
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                          <FormControl
                            size="small"
                            sx={{ width: 86, flexShrink: 0 }}
                          >
                            <InputLabel id="comparison-order-label">
                              Order
                            </InputLabel>
                            <Select
                              labelId="comparison-order-label"
                              label="Order"
                              value={view.order}
                              renderValue={(value) =>
                                value === "source" ? "Source" : "Priority"
                              }
                              sx={{
                                fontSize: 13,
                                "& .MuiSelect-select": {
                                  pl: 1,
                                  pr: "24px !important",
                                },
                                "& .MuiSelect-icon": {
                                  right: 3,
                                  fontSize: 20,
                                },
                              }}
                              onChange={(event) =>
                                changeView({ order: event.target.value })
                              }
                            >
                              <MenuItem value="priority">Priority</MenuItem>
                              <MenuItem value="source">Source order</MenuItem>
                            </Select>
                          </FormControl>
                        </Stack>
                      </Box>
                      <List
                        disablePadding
                        sx={{
                          flex: 1,
                          overflowY: "auto",
                          overflowAnchor: "none",
                          scrollbarGutter: "stable",
                          minHeight: 100,
                        }}
                      >
                        {visible.map((requirement) => (
                          <ListItemButton
                            key={requirement.requirement_id}
                            selected={
                              selected?.requirement_id ===
                              requirement.requirement_id
                            }
                            aria-label={requirement.test_name}
                            aria-current={
                              selected?.requirement_id ===
                              requirement.requirement_id
                                ? "true"
                                : undefined
                            }
                            onClick={() => {
                              updateView({
                                selectedId: requirement.requirement_id,
                              });
                              if (detailPane.current)
                                detailPane.current.scrollTop = 0;
                            }}
                            sx={{
                              display: "block",
                              px: 1.5,
                              py: 1.75,
                              borderBottom: "1px solid",
                              borderColor: "divider",
                              borderLeft: "3px solid transparent",
                              "&.Mui-selected": {
                                bgcolor: "#edf3ff",
                                borderLeftColor: "primary.main",
                              },
                              "&.Mui-selected:hover": { bgcolor: "#e4edff" },
                            }}
                          >
                            <Typography
                              variant="body2"
                              fontWeight={700}
                              sx={{
                                overflowWrap: "anywhere",
                                mb: 0.5,
                                fontSize: 14,
                                lineHeight: 1.45,
                              }}
                            >
                              {requirement.test_name}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{
                                display: "block",
                                mb: 0.75,
                                overflowWrap: "anywhere",
                              }}
                            >
                              {requirement.methods.join(" · ") ||
                                "Customer procedure"}
                            </Typography>
                            <Stack
                              direction="row"
                              gap={0.75}
                              alignItems="center"
                              flexWrap="wrap"
                            >
                              <Chip
                                size="small"
                                sx={statusStyle(requirement.comparison.status)}
                                label={
                                  resultLabel(requirement.comparison.status)
                                    .label
                                }
                              />
                              {report.assessment_reviews?.[
                                requirement.requirement_id
                              ]?.review_status === "accepted" && (
                                <Typography
                                  variant="caption"
                                  color="success.main"
                                  fontWeight={650}
                                >
                                  Reviewed
                                </Typography>
                              )}
                            </Stack>
                          </ListItemButton>
                        ))}
                        {!visible.length && (
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ p: 2 }}
                          >
                            No tests match this filter.
                          </Typography>
                        )}
                      </List>
                    </Paper>
                    <Paper
                      variant="outlined"
                      sx={{
                        minWidth: 0,
                        minHeight: { xs: 380, md: 0 },
                        display: "flex",
                        flexDirection: "column",
                        overflow: "hidden",
                      }}
                    >
                      <Box
                        ref={detailPane}
                        data-testid="report-detail-pane"
                        role="region"
                        aria-label="Selected test details"
                        tabIndex={-1}
                        sx={{
                          flex: 1,
                          minHeight: 0,
                          overflowY: { md: "auto" },
                          overflowAnchor: "none",
                          scrollbarGutter: "stable",
                          outline: "none",
                        }}
                      >
                        {selected ? (
                          <>
                            <Box
                              sx={{
                                px: 2,
                                py: 1.75,
                                borderBottom: "1px solid #e3e9ee",
                                bgcolor: "background.paper",
                              }}
                            >
                              <Stack
                                direction="row"
                                justifyContent="space-between"
                                alignItems="center"
                                spacing={1.5}
                              >
                                <Typography
                                  variant="h6"
                                  sx={{
                                    overflowWrap: "anywhere",
                                    fontSize: 19,
                                    lineHeight: 1.35,
                                    flex: 1,
                                    minWidth: 0,
                                  }}
                                >
                                  {selected.test_name}
                                </Typography>
                                <Chip
                                  size="small"
                                  sx={statusStyle(selected.comparison.status)}
                                  label={
                                    resultLabel(selected.comparison.status)
                                      .label
                                  }
                                />
                                <Tooltip title="Check source PDF">
                                  <IconButton
                                    component={RouterLink}
                                    to={sourceUrl(
                                      documentId,
                                      selected.requirement_id,
                                    )}
                                    aria-label="Check source PDF"
                                    size="small"
                                    sx={{
                                      flexShrink: 0,
                                      color: "primary.main",
                                    }}
                                  >
                                    <OpenInNewRoundedIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              </Stack>
                              {selected.comparison.status !== "excluded" &&
                                selected.comparison.checks.length > 0 && (
                                  <Stack
                                    direction="row"
                                    gap={2}
                                    flexWrap="wrap"
                                    role="group"
                                    aria-label="Comparison checks"
                                    sx={{ mt: 1 }}
                                  >
                                    {[
                                      ["supported", "matched", "#24745a"],
                                      ["gap", "gaps", "#b34552"],
                                      ["unknown", "to clarify", "#946018"],
                                    ].map(([state, label, color]) => {
                                      const count =
                                        selected.comparison.checks.filter(
                                          (check) => check.status === state,
                                        ).length;
                                      return count > 0 ? (
                                        <Typography
                                          key={state}
                                          variant="caption"
                                          sx={{ color, fontWeight: 600 }}
                                        >
                                          {count}{" "}
                                          {state === "gap" && count === 1
                                            ? "gap"
                                            : label}
                                        </Typography>
                                      ) : null;
                                    })}
                                  </Stack>
                                )}
                              {["needs_attention", "needs_review"].includes(
                                selected.source_review_state,
                              ) && (
                                <Alert severity="warning" sx={{ mt: 1.5 }}>
                                  Source details need review.
                                </Alert>
                              )}
                            </Box>
                            {selected.comparison.status !== "excluded" ? (
                              <ReportDimensions
                                requirement={selected}
                                expanded={expanded[selectedKey] ?? {}}
                                onToggle={(group, open) =>
                                  setExpanded((current) => ({
                                    ...current,
                                    [selectedKey]: {
                                      ...current[selectedKey],
                                      [group]: open,
                                    },
                                  }))
                                }
                              />
                            ) : (
                              <Typography
                                variant="body2"
                                color="text.secondary"
                                sx={{ px: 2.5, pb: 2 }}
                              >
                                This test is outside the report's active scope.
                                Its source requirements remain saved.
                              </Typography>
                            )}
                            {actions.length > 0 && (
                              <Stack
                                direction="row"
                                spacing={1.25}
                                useFlexGap
                                flexWrap="wrap"
                                sx={{ px: 2, py: 1.5 }}
                              >
                                {actions.map((action) => (
                                  <Tooltip
                                    key={action.label}
                                    title={action.description}
                                    describeChild
                                  >
                                    <Button
                                      component={RouterLink}
                                      to={action.to}
                                      size="small"
                                      endIcon={<ArrowForwardRoundedIcon />}
                                      sx={{ fontSize: 13, px: 0 }}
                                    >
                                      {action.label}
                                    </Button>
                                  </Tooltip>
                                ))}
                              </Stack>
                            )}
                            <ReportAssessmentReview
                              key={`${report.comparison_id}-${selected.requirement_id}`}
                              documentId={documentId}
                              report={report}
                              requirement={selected}
                              onSaved={(updated) => data.setData(updated)}
                            />
                            <ReportScopeControls
                              documentId={documentId}
                              report={report}
                              requirement={selected}
                              onSaved={scopeSaved}
                            />
                          </>
                        ) : (
                          <Stack
                            alignItems="center"
                            justifyContent="center"
                            sx={{ minHeight: 320, height: "100%", p: 3 }}
                          >
                            <Typography variant="subtitle1">
                              No tests in this view
                            </Typography>
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              sx={{ mt: 0.75, textAlign: "center" }}
                            >
                              Your filter is still applied. Change it or clear
                              the search to see other tests.
                            </Typography>
                            <Button
                              sx={{ mt: 1 }}
                              onClick={() => {
                                changeView({ filter: "all", query: "" });
                                requestAnimationFrame(() =>
                                  document
                                    .querySelector<HTMLElement>(
                                      "[data-report-focus]",
                                    )
                                    ?.focus({ preventScroll: true }),
                                );
                              }}
                            >
                              Show all tests
                            </Button>
                          </Stack>
                        )}
                      </Box>
                    </Paper>
                  </Box>
                </>
              )}
              {tab === "report" && (
                <Box sx={{ overflowY: "auto", minHeight: 0, flex: 1 }}>
                  {" "}
                  <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 } }}>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      justifyContent="space-between"
                      alignItems={{ sm: "center" }}
                      spacing={2}
                      sx={{ mb: 2 }}
                    >
                      <Box>
                        <Typography variant="h6">Draft gap report</Typography>
                        <Typography variant="body2" color="text.secondary">
                          Saved comparison from{" "}
                          {new Date(report.created_at).toLocaleString()}.
                        </Typography>
                      </Box>
                      <Stack direction="row" spacing={1}>
                        <Button
                          component="a"
                          href={`${reportBase}/pdf?download=true`}
                          download
                          variant="contained"
                          startIcon={<DownloadRoundedIcon />}
                        >
                          Download PDF
                        </Button>
                        <Button
                          component="a"
                          href={`${reportBase}/json`}
                          download
                          variant="outlined"
                        >
                          JSON
                        </Button>
                      </Stack>
                    </Stack>
                    {snapshot.error && (
                      <Alert
                        severity="error"
                        sx={{ mb: 2 }}
                        action={
                          <Button onClick={snapshot.refresh}>Retry</Button>
                        }
                      >
                        {snapshot.error}
                      </Alert>
                    )}
                    <PdfPageViewer
                      title="Report preview"
                      imageUrl={pageImage}
                      alt={`Gap report page ${page}`}
                      loading={pageLoading}
                      error={pageError}
                      documentUrl={`${reportBase}/pdf`}
                      height="75vh"
                      navigation={
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Button
                            size="small"
                            aria-label="Previous report page"
                            disabled={page <= 1}
                            onClick={() => setPage((value) => value - 1)}
                            startIcon={<ChevronLeftRoundedIcon />}
                          >
                            Previous
                          </Button>
                          <Typography
                            variant="body2"
                            sx={{ whiteSpace: "nowrap" }}
                          >
                            Page {page} of {pages}
                          </Typography>
                          <Button
                            size="small"
                            aria-label="Next report page"
                            disabled={page >= pages}
                            onClick={() => setPage((value) => value + 1)}
                            endIcon={<ChevronRightRoundedIcon />}
                          >
                            Next
                          </Button>
                        </Stack>
                      }
                    />
                  </Paper>
                </Box>
              )}
            </>
          )}
        </>
      )}
      <Snackbar
        open={Boolean(notice)}
        autoHideDuration={6500}
        onClose={() => setNotice("")}
        message={notice}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      />
    </Box>
  );
}
