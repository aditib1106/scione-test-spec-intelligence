import { useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  InputAdornment,
  InputLabel,
  List,
  ListItemButton,
  MenuItem,
  Paper,
  Select,
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
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import DownloadRoundedIcon from "@mui/icons-material/DownloadRounded";
import OpenInNewRoundedIcon from "@mui/icons-material/OpenInNewRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import { useResource } from "../api";
import PdfPageViewer from "../components/PdfPageViewer";
import FactSummary from "../components/FactSummary";
import MatchingControls from "../components/MatchingControls";
import ReportScopeControls from "../components/ReportScopeControls";
import type { PageProps } from "../types";
import type { LibraryDocument } from "../libraryTypes";
import type {
  ComparisonCheck,
  ComparisonData,
  ComparisonRequirement,
  ComparisonStatus,
} from "../comparisonTypes";

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
    tone: "#66758b",
  },
  excluded: { label: "Excluded", color: "default", tone: "#66758b" },
};

function resultLabel(status: ComparisonStatus) {
  return results[status] ?? results.needs_information;
}

function sourceUrl(documentId: string, requirementId: string) {
  return `/specifications?${new URLSearchParams({ document: documentId, requirement: requirementId })}`;
}

function stageLabel(value: string) {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function stepLabel(check: ComparisonCheck, siblings: ComparisonCheck[]) {
  const claim = check.requirement_claim;
  if (!claim?.sequence) return claim?.configuration || "";
  const temperatures = [
    ...new Set(
      siblings
        .filter(
          (item) =>
            item.parameter_id === "temperature" &&
            item.stage === check.stage &&
            item.requirement_claim?.sequence === claim.sequence &&
            (item.requirement_claim?.configuration ?? null) ===
              (claim.configuration ?? null),
        )
        .map((item) => item.requested),
    ),
  ];
  const temperature =
    check.parameter_id !== "temperature" && temperatures.length === 1
      ? ` · ${temperatures[0]}`
      : "";
  return `Step ${claim.sequence}${temperature}${claim.configuration ? ` · ${claim.configuration}` : ""}`;
}

function groupChecks(
  checks: ComparisonCheck[],
  type: "method" | "condition" | "criterion",
) {
  return checks.filter((check) => {
    if (type === "method")
      return check.kind === "method" || check.parameter_id === "method";
    if (type === "criterion")
      return /criter|report|measurement/.test(check.kind);
    return /condition|scope|instruction/.test(check.kind);
  });
}

function dimension(
  checks: ComparisonCheck[],
  type: "method" | "condition" | "criterion",
) {
  const relevant = groupChecks(checks, type);
  if (relevant.some((check) => check.status === "gap"))
    return { label: "Gap", color: "error" as const };
  if (relevant.some((check) => check.status === "unknown"))
    return { label: "Needs information", color: "warning" as const };
  if (relevant.some((check) => check.status === "supported"))
    return { label: "Covered", color: "success" as const };
  return {
    label: relevant.length ? "Recorded" : "Not assessed",
    color: "default" as const,
  };
}

function summaryFacts(
  checks: ComparisonCheck[],
  side: "requested" | "declared",
) {
  return checks
    .filter(
      (check) =>
        check.requested &&
        check.requirement_claim?.normalization_state !== "unresolved",
    )
    .map((check) => ({
      label: check.label,
      value: check[side] || "Not recorded",
      context: check.requirement_claim?.sequence
        ? `step ${check.requirement_claim.sequence}`
        : check.stage && !["general", "measurement"].includes(check.stage)
          ? stageLabel(check.stage)
          : undefined,
    }));
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
  if (methodQuestion)
    actions.push({
      label: "Review test and method",
      to: `/reports/advanced?${new URLSearchParams({ document: documentId })}`,
      description: "Confirm which test and method the customer requests.",
    });
  if (actions.length) return actions;
  const methodGap = requirement.comparison.checks.some(
    (check) =>
      check.status === "gap" &&
      (check.kind === "method" || check.parameter_id === "test_method"),
  );
  if (!requirement.capability_id && methodGap)
    return [
      {
        label: "Add a lab capability",
        to: "/my-lab",
        description:
          "This method is not listed. Add a declaration if your lab can offer it.",
      },
    ];
  if (unknown.length && requirement.capability_id)
    return [
      {
        label: "Update lab information",
        to: `/my-lab?${new URLSearchParams({ capability: requirement.capability_id })}`,
        description:
          "Add the missing lab details, then recompare without analyzing the customer PDF again.",
      },
    ];
  return actions;
}

export default function Comparison({ workspace }: PageProps) {
  const [params, setParams] = useSearchParams();
  const docs = workspace.documents.filter((document) => document.latest_run_id);
  const documentId = params.get("document") || docs[0]?.document_id || "";
  const data = useResource<ComparisonData>(
    documentId ? `/api/comparisons/${encodeURIComponent(documentId)}` : null,
  );
  const library = useResource<{ documents: LibraryDocument[] }>("/api/library");
  const [selectedId, setSelectedId] = useState("");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [tab, setTab] = useState("comparison");
  const [page, setPage] = useState(1);
  const [pageImage, setPageImage] = useState("");
  const [pageLoading, setPageLoading] = useState(false);
  const [pageError, setPageError] = useState("");
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

  useEffect(() => {
    setSelectedId("");
    setFilter("all");
    setQuery("");
    setPage(1);
  }, [documentId]);

  useEffect(() => {
    setPage(1);
  }, [data.data?.comparison_id]);

  const report = data.data;
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return (report?.requirements ?? []).filter(
      (item) =>
        (filter === "all" || item.comparison.status === filter) &&
        [item.test_name, ...item.methods]
          .join(" ")
          .toLocaleLowerCase()
          .includes(needle),
    );
  }, [report, filter, query]);
  const selected =
    visible.find((item) => item.requirement_id === selectedId) || visible[0];
  const actions = selected ? nextActions(documentId, selected) : [];
  useEffect(() => setDetailsOpen(false), [selected?.requirement_id]);
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
    fetch(`${reportBase}/pages/${page}`, { signal: controller.signal })
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

  return (
    <Box
      sx={{
        maxWidth: 1440,
        mx: "auto",
        px: { xs: 2, md: 4 },
        py: { xs: 3, md: 4 },
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        spacing={2}
        sx={{ mb: 3 }}
      >
        <Box>
          <Typography variant="overline" color="primary.main">
            LAB CAPABILITY REVIEW
          </Typography>
          <Typography variant="h4">Gap report</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            See what your lab can cover and what needs attention.
          </Typography>
        </Box>
        {docs.length > 0 && (
          <FormControl size="small" sx={{ width: { xs: "100%", sm: 350 } }}>
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
              onChange={(event) => setParams({ document: event.target.value })}
              sx={{ bgcolor: "background.paper" }}
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
      </Stack>

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
              sx={{ mb: 2 }}
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
              <Paper variant="outlined" sx={{ p: { xs: 2, md: 2.5 }, mb: 2.5 }}>
                <Stack
                  direction={{ xs: "column", sm: "row" }}
                  justifyContent="space-between"
                  spacing={2}
                  sx={{ mb: 2.5 }}
                >
                  <Box>
                    <Typography variant="subtitle1">
                      {report.document.title ||
                        report.document.specification_id ||
                        report.document.original_name}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {report.lab_name} ·{" "}
                      {report.requirements.length -
                        (report.summary.excluded ?? 0)}{" "}
                      tests in scope
                      {(report.summary.excluded ?? 0) > 0 && (
                        <>
                          {" "}
                          ·{" "}
                          <Button
                            size="small"
                            onClick={() => {
                              setFilter("excluded");
                              setTab("comparison");
                            }}
                            sx={{
                              minWidth: 0,
                              py: 0,
                              verticalAlign: "baseline",
                            }}
                          >
                            {report.summary.excluded} excluded
                          </Button>
                        </>
                      )}
                    </Typography>
                  </Box>
                  <Button
                    variant="outlined"
                    size="small"
                    startIcon={
                      data.loading ? (
                        <CircularProgress size={14} />
                      ) : (
                        <RefreshRoundedIcon />
                      )
                    }
                    disabled={data.loading}
                    onClick={data.refresh}
                  >
                    Recompare saved information
                  </Button>
                </Stack>
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: {
                      xs: "repeat(2,1fr)",
                      md: "repeat(4,1fr)",
                    },
                    gap: 2,
                  }}
                >
                  {(
                    [
                      "fully_matched",
                      "partially_matched",
                      "not_matched",
                      "needs_information",
                    ] as ComparisonStatus[]
                  ).map((status, index) => (
                    <Box
                      key={status}
                      sx={{
                        borderLeft: { md: index ? "1px solid #e2e8f0" : 0 },
                        pl: { md: index ? 3 : 0 },
                      }}
                    >
                      <Typography
                        sx={{
                          fontSize: 28,
                          fontWeight: 700,
                          color: results[status].tone,
                          lineHeight: 1.2,
                        }}
                      >
                        {report.summary[status] ?? 0}
                      </Typography>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ mt: 0.5 }}
                      >
                        {results[status].label}
                      </Typography>
                    </Box>
                  ))}
                </Box>
                <MatchingControls
                  key={documentId}
                  documentId={documentId}
                  comparisonId={report.comparison_id}
                  aiApplied={
                    report.comparison_engine === "ai_semantic_matching"
                  }
                  providers={workspace.providers}
                  onComplete={data.refresh}
                />
              </Paper>

              <Tabs
                value={tab}
                variant="scrollable"
                scrollButtons="auto"
                allowScrollButtonsMobile
                onChange={(_, value: string) => setTab(value)}
                aria-label="Gap report views"
                sx={{
                  mb: 2.5,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Tab label="Comparison" value="comparison" />
                <Tab label="PDF & download" value="report" />
                <Tab
                  label="Advanced review"
                  value="advanced"
                  component={RouterLink}
                  to={`/reports/advanced?${new URLSearchParams({ document: documentId })}`}
                />
              </Tabs>

              {tab === "comparison" && (
                <>
                  <Box
                    sx={{
                      display: "grid",
                      gridTemplateColumns: {
                        xs: "minmax(0, 1fr)",
                        md: "290px minmax(0, 1fr)",
                      },
                      alignItems: "start",
                      gap: 2.5,
                    }}
                  >
                    <Paper
                      variant="outlined"
                      component="nav"
                      aria-label="Requested tests"
                      sx={{
                        overflow: "hidden",
                        position: { md: "sticky" },
                        top: 80,
                      }}
                    >
                      <Box
                        sx={{
                          p: 2,
                          borderBottom: "1px solid",
                          borderColor: "divider",
                        }}
                      >
                        <Typography variant="subtitle1" sx={{ mb: 1.5 }}>
                          Requested tests
                        </Typography>
                        <TextField
                          fullWidth
                          size="small"
                          placeholder="Find a test or method"
                          value={query}
                          onChange={(event) => setQuery(event.target.value)}
                          inputProps={{
                            "aria-label": "Search requested tests",
                          }}
                          InputProps={{
                            startAdornment: (
                              <InputAdornment position="start">
                                <SearchRoundedIcon fontSize="small" />
                              </InputAdornment>
                            ),
                          }}
                          sx={{ mb: 1.5 }}
                        />
                        <FormControl fullWidth size="small">
                          <InputLabel id="comparison-result-label">
                            Show
                          </InputLabel>
                          <Select
                            labelId="comparison-result-label"
                            label="Show"
                            value={filter}
                            onChange={(event) => setFilter(event.target.value)}
                          >
                            <MenuItem value="all">All tests</MenuItem>
                            {Object.entries(results).map(([value, state]) => (
                              <MenuItem key={value} value={value}>
                                {state.label}
                              </MenuItem>
                            ))}
                          </Select>
                        </FormControl>
                      </Box>
                      <List
                        disablePadding
                        sx={{
                          maxHeight: { xs: 260, md: "calc(100vh - 345px)" },
                          overflowY: "auto",
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
                            onClick={() =>
                              setSelectedId(requirement.requirement_id)
                            }
                            sx={{
                              display: "block",
                              p: 2,
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
                              fontWeight={600}
                              sx={{ overflowWrap: "anywhere" }}
                            >
                              {requirement.test_name}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{
                                display: "block",
                                mt: 0.35,
                                mb: 1,
                                overflowWrap: "anywhere",
                              }}
                            >
                              {requirement.methods.join(" · ") ||
                                "Customer procedure"}
                            </Typography>
                            <Chip
                              size="small"
                              variant="outlined"
                              color={
                                resultLabel(requirement.comparison.status).color
                              }
                              label={
                                resultLabel(requirement.comparison.status).label
                              }
                            />
                          </ListItemButton>
                        ))}
                        {!visible.length && (
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ p: 3 }}
                          >
                            No tests match this filter.
                          </Typography>
                        )}
                      </List>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{
                          display: "block",
                          px: 2,
                          py: 1.25,
                          bgcolor: "#f8fafc",
                        }}
                      >
                        {visible.length} of {report.requirements.length} tests
                      </Typography>
                    </Paper>
                    {selected ? (
                      <Paper
                        key={selected.requirement_id}
                        variant="outlined"
                        sx={{ overflow: "hidden", minWidth: 0 }}
                      >
                        <Box
                          sx={{
                            p: { xs: 2, md: 2.5 },
                            borderBottom: "1px solid",
                            borderColor: "divider",
                          }}
                        >
                          <Stack
                            direction={{ xs: "column", sm: "row" }}
                            justifyContent="space-between"
                            alignItems={{ sm: "flex-start" }}
                            spacing={1.5}
                          >
                            <Box sx={{ minWidth: 0 }}>
                              <Typography
                                variant="h6"
                                sx={{ overflowWrap: "anywhere" }}
                              >
                                {selected.test_name}
                              </Typography>
                              <Chip
                                sx={{ mt: 1 }}
                                size="small"
                                color={
                                  resultLabel(selected.comparison.status).color
                                }
                                label={
                                  resultLabel(selected.comparison.status).label
                                }
                              />
                            </Box>
                            <Button
                              component={RouterLink}
                              to={sourceUrl(
                                documentId,
                                selected.requirement_id,
                              )}
                              startIcon={<OpenInNewRoundedIcon />}
                              sx={{ flexShrink: 0, alignSelf: "flex-start" }}
                            >
                              Check source PDF
                            </Button>
                          </Stack>
                          {selected.source_review_state === "needs_attention" ||
                          selected.source_review_state === "needs_review" ? (
                            <Alert severity="warning" sx={{ mt: 2 }}>
                              Check the extracted requirements against the PDF
                              before relying on this result.
                            </Alert>
                          ) : null}
                        </Box>
                        {selected.comparison.status !== "excluded" ? (
                          <>
                            <TableContainer>
                              <Table
                                size="small"
                                aria-label="Test capability comparison"
                                sx={{
                                  "& td": { verticalAlign: "top", py: 2 },
                                  "& th": { py: 1.5 },
                                }}
                              >
                                <TableHead>
                                  <TableRow>
                                    <TableCell sx={{ width: "23%" }}>
                                      Compare
                                    </TableCell>
                                    <TableCell sx={{ width: "39%" }}>
                                      Customer requirement
                                    </TableCell>
                                    <TableCell sx={{ width: "38%" }}>
                                      Your lab
                                    </TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {(
                                    [
                                      ["method", "Method"],
                                      ["condition", "Conditions"],
                                      ["criterion", "Criteria / reporting"],
                                    ] as const
                                  ).map(([type, label]) => {
                                    const checks = groupChecks(
                                      selected.comparison.checks,
                                      type,
                                    );
                                    const state = dimension(
                                      selected.comparison.checks,
                                      type,
                                    );
                                    const methodCheck = checks.find(
                                      (check) => check.kind === "method",
                                    );
                                    return (
                                      <TableRow key={type}>
                                        <TableCell sx={{ minWidth: 115 }}>
                                          <Typography
                                            variant="body2"
                                            fontWeight={600}
                                          >
                                            {label}
                                          </Typography>
                                          <Chip
                                            size="small"
                                            variant="outlined"
                                            color={state.color}
                                            label={state.label}
                                            sx={{
                                              mt: 0.75,
                                              height: "auto",
                                              minHeight: 23,
                                              "& .MuiChip-label": {
                                                whiteSpace: "normal",
                                                py: 0.25,
                                              },
                                            }}
                                          />
                                        </TableCell>
                                        <TableCell sx={{ minWidth: 155 }}>
                                          {type === "method" ? (
                                            <Typography
                                              variant="body2"
                                              sx={{ overflowWrap: "anywhere" }}
                                            >
                                              {selected.methods.join(" · ") ||
                                                methodCheck?.requested ||
                                                "Customer-defined procedure"}
                                            </Typography>
                                          ) : (
                                            <FactSummary
                                              facts={summaryFacts(
                                                checks,
                                                "requested",
                                              )}
                                              emptyLabel={
                                                checks.length
                                                  ? "See source details"
                                                  : "Not specified"
                                              }
                                            />
                                          )}
                                        </TableCell>
                                        <TableCell sx={{ minWidth: 155 }}>
                                          {type === "method" ? (
                                            <Typography
                                              variant="body2"
                                              sx={{ overflowWrap: "anywhere" }}
                                            >
                                              {methodCheck?.declared ||
                                                (methodCheck?.status ===
                                                "supported"
                                                  ? "Matching method listed"
                                                  : methodCheck?.status ===
                                                      "gap"
                                                    ? "No matching method listed"
                                                    : "Match not established")}
                                            </Typography>
                                          ) : (
                                            <FactSummary
                                              facts={summaryFacts(
                                                checks,
                                                "declared",
                                              )}
                                              emptyLabel="Not recorded for this request"
                                            />
                                          )}
                                        </TableCell>
                                      </TableRow>
                                    );
                                  })}
                                </TableBody>
                              </Table>
                            </TableContainer>
                            <Accordion
                              expanded={detailsOpen}
                              onChange={(_, expanded) =>
                                setDetailsOpen(expanded)
                              }
                              disableGutters
                              elevation={0}
                              sx={{
                                borderTop: "1px solid",
                                borderColor: "divider",
                                "&:before": { display: "none" },
                              }}
                            >
                              <AccordionSummary
                                expandIcon={<ExpandMoreRoundedIcon />}
                                sx={{ px: { xs: 2, md: 2.5 } }}
                              >
                                <Box>
                                  <Typography variant="body2" fontWeight={600}>
                                    View detailed comparison (
                                    {selected.comparison.checks.length} checks)
                                  </Typography>
                                  <Typography
                                    variant="caption"
                                    color="text.secondary"
                                  >
                                    Parameters, stages and reasons for each
                                    result
                                  </Typography>
                                </Box>
                              </AccordionSummary>
                              <AccordionDetails sx={{ p: 0 }}>
                                <TableContainer sx={{ maxHeight: 480 }}>
                                  <Table
                                    stickyHeader
                                    size="small"
                                    aria-label={`${selected.test_name} requirement details`}
                                  >
                                    <TableHead>
                                      <TableRow>
                                        <TableCell>Requirement</TableCell>
                                        <TableCell>Customer asks for</TableCell>
                                        <TableCell>Your lab records</TableCell>
                                        <TableCell>Result and reason</TableCell>
                                      </TableRow>
                                    </TableHead>
                                    <TableBody>
                                      {selected.comparison.checks.map(
                                        (check, index) => (
                                          <TableRow
                                            key={`${check.parameter_id}-${index}`}
                                          >
                                            <TableCell sx={{ minWidth: 140 }}>
                                              <Typography
                                                variant="body2"
                                                fontWeight={600}
                                              >
                                                {check.label}
                                              </Typography>
                                              {check.stage && (
                                                <Typography
                                                  variant="caption"
                                                  color="text.secondary"
                                                >
                                                  {stageLabel(check.stage)}
                                                </Typography>
                                              )}
                                              {stepLabel(
                                                check,
                                                selected.comparison.checks,
                                              ) && (
                                                <Typography
                                                  variant="caption"
                                                  display="block"
                                                  color="text.secondary"
                                                >
                                                  {stepLabel(
                                                    check,
                                                    selected.comparison.checks,
                                                  )}
                                                </Typography>
                                              )}
                                            </TableCell>
                                            <TableCell
                                              sx={{
                                                minWidth: 180,
                                                maxWidth: 300,
                                                overflowWrap: "anywhere",
                                              }}
                                            >
                                              {check.requested}
                                            </TableCell>
                                            <TableCell
                                              sx={{
                                                minWidth: 180,
                                                maxWidth: 300,
                                                overflowWrap: "anywhere",
                                              }}
                                            >
                                              {check.declared || (
                                                <Typography
                                                  variant="body2"
                                                  color="text.secondary"
                                                >
                                                  Not recorded
                                                </Typography>
                                              )}
                                            </TableCell>
                                            <TableCell
                                              sx={{
                                                minWidth: 230,
                                                maxWidth: 380,
                                              }}
                                            >
                                              <Typography
                                                variant="body2"
                                                fontWeight={600}
                                                color={
                                                  check.status === "supported"
                                                    ? "success.main"
                                                    : check.status === "gap"
                                                      ? "error.main"
                                                      : check.status ===
                                                          "unknown"
                                                        ? "warning.main"
                                                        : "text.secondary"
                                                }
                                              >
                                                {check.status === "supported"
                                                  ? "Covered"
                                                  : check.status === "gap"
                                                    ? "Capability gap"
                                                    : check.status === "unknown"
                                                      ? "Needs information"
                                                      : "Recorded for reference"}
                                              </Typography>
                                              <Typography
                                                variant="caption"
                                                color="text.secondary"
                                              >
                                                {check.reason}
                                              </Typography>
                                            </TableCell>
                                          </TableRow>
                                        ),
                                      )}
                                    </TableBody>
                                  </Table>
                                </TableContainer>
                              </AccordionDetails>
                            </Accordion>
                          </>
                        ) : (
                          <Box sx={{ p: { xs: 2, md: 2.5 } }}>
                            <Typography variant="body2" color="text.secondary">
                              This test is outside the report's active scope.
                              Its source requirements remain saved.
                            </Typography>
                          </Box>
                        )}
                        {actions.length > 0 && (
                          <Box
                            sx={{
                              p: { xs: 2, md: 2.5 },
                              bgcolor: "#f8fafc",
                              borderTop: "1px solid",
                              borderColor: "divider",
                            }}
                          >
                            <Typography variant="body2" fontWeight={600}>
                              Next step
                            </Typography>
                            {actions.map((action) => (
                              <Box key={action.label} sx={{ mt: 1 }}>
                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                  display="block"
                                >
                                  {action.description}
                                </Typography>
                                <Button
                                  component={RouterLink}
                                  to={action.to}
                                  endIcon={<ArrowForwardRoundedIcon />}
                                  sx={{ mt: 0.5, px: 0 }}
                                >
                                  {action.label}
                                </Button>
                              </Box>
                            ))}
                          </Box>
                        )}
                        <ReportScopeControls
                          key={`${documentId}-${selected.requirement_id}`}
                          documentId={documentId}
                          report={report}
                          requirement={selected}
                          onSaved={(updated) => {
                            const next = updated.requirements.find(
                              (item) =>
                                item.requirement_id === selected.requirement_id,
                            );
                            if (
                              filter !== "all" &&
                              next?.comparison.status !== filter
                            )
                              setFilter("all");
                            data.setData(updated);
                          }}
                        />
                      </Paper>
                    ) : (
                      <Paper
                        variant="outlined"
                        sx={{ p: 5, textAlign: "center" }}
                      >
                        <Typography variant="subtitle1">
                          No tests in this view
                        </Typography>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ mt: 1 }}
                        >
                          Change the search or filter to see the other requested
                          tests.
                        </Typography>
                        <Button
                          sx={{ mt: 1 }}
                          onClick={() => {
                            setFilter("all");
                            setQuery("");
                          }}
                        >
                          Show all tests
                        </Button>
                      </Paper>
                    )}
                  </Box>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mt: 2 }}
                  >
                    Coverage describes the lab's declared ability to perform and
                    report a test. It does not predict whether a customer sample
                    will pass.
                  </Typography>
                </>
              )}

              {tab === "report" && (
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
                      action={<Button onClick={snapshot.refresh}>Retry</Button>}
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
              )}
            </>
          )}
        </>
      )}
    </Box>
  );
}
