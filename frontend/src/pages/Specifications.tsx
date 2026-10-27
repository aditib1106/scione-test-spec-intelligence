import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import UploadFileOutlinedIcon from "@mui/icons-material/UploadFileOutlined";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import { ApiError, request } from "../api";
import type { PageProps, Requirement, SpecificationData } from "../types";

const blue = "#2459cc";
const pageBg = "#f5f7fb";
type Decision = "confirmed" | "excluded" | "needs_review";
type Filter = "pending" | "all";

const panelSx = {
  border: "1px solid #e2e7f0",
  borderRadius: 2.5,
  bgcolor: "#fff",
  boxShadow: "0 1px 3px rgba(20,35,70,.04)",
};
const states: Record<
  string,
  { label: string; color: "warning" | "success" | "default" }
> = {
  needs_attention: { label: "Needs review", color: "warning" },
  needs_review: { label: "Needs review", color: "warning" },
  ready: { label: "Extracted", color: "default" },
  confirmed: { label: "Confirmed", color: "success" },
  excluded: { label: "Excluded", color: "default" },
};

function errorText(error: unknown) {
  return error instanceof Error
    ? error.message
    : "The request could not be completed. Please try again.";
}
function statusOf(data: SpecificationData, id: string) {
  const decision = data.review?.decisions[id]?.status;
  if (decision === "needs_review") return "needs_attention";
  if (decision === "confirmed" || decision === "excluded") return decision;
  return data.assessments[id]?.state ?? "ready";
}
function valueText(value: {
  name: string;
  raw_value: string;
  unit?: string | null;
  context?: string | null;
}) {
  return `${value.raw_value}${value.unit && !value.raw_value.includes(value.unit) ? ` ${value.unit}` : ""}`;
}

export default function Specifications({ workspace, onRefresh }: PageProps) {
  const [params, setParams] = useSearchParams();
  const lastRequirementParam = useRef(params.get("requirement"));
  const navigate = useNavigate();
  const compact = useMediaQuery("(max-width:1099px)");
  const docs = workspace.documents;
  const requestedId = params.get("document");
  const documentId = requestedId || docs[0]?.document_id || "";
  const activeDocumentRef = useRef(documentId);
  activeDocumentRef.current = documentId;
  const [data, setData] = useState<SpecificationData | null>(null);
  const dataRef = useRef(data);
  dataRef.current = data;
  const [loading, setLoading] = useState(Boolean(documentId));
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("pending");
  const [selectedId, setSelectedId] = useState(params.get("requirement") ?? "");
  const [page, setPage] = useState(1);
  const [pageImage, setPageImage] = useState("");
  const [pageError, setPageError] = useState("");
  const [pageLoading, setPageLoading] = useState(false);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [provider, setProvider] = useState(
    workspace.providers.find((item) => item.available)?.id ??
      workspace.providers[0]?.id ??
      "",
  );
  const selectedProvider = workspace.providers.find(
    (item) => item.id === provider,
  );
  const [model, setModel] = useState(selectedProvider?.model ?? "");
  const [strategy, setStrategy] = useState<
    "compact_document" | "whole_document" | "focused_passes"
  >("compact_document");
  const [outputLimit, setOutputLimit] = useState(
    selectedProvider?.output_limit ?? 4096,
  );
  const [permission, setPermission] = useState(false);
  const [freeTierConfirmed, setFreeTierConfirmed] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [batchNote, setBatchNote] = useState("");
  const [batchAck, setBatchAck] = useState(false);
  const [batchSaving, setBatchSaving] = useState(false);
  const [batchError, setBatchError] = useState("");
  const [analysisOpen, setAnalysisOpen] = useState(!data?.run);

  useEffect(() => {
    setBatchIds([]);
    setBatchAck(false);
    setBatchNote("");
    setBatchError("");
  }, [documentId, data?.run?.run_id]);

  const load = useCallback(
    async (
      id: string,
      signal?: AbortSignal,
      preferredRequirementId?: string | null,
    ) => {
      if (!id) {
        setData(null);
        setLoading(false);
        return;
      }
      setLoading(true);
      setError("");
      try {
        const next = await request<SpecificationData>(
          `/api/specifications/${encodeURIComponent(id)}`,
          { signal },
        );
        if (!signal?.aborted) {
          setData(next);
          setPage(1);
          setAnalysisError("");
          setSelectedId((current) => {
            const ids = next.run?.extraction.requirements ?? [];
            const preferred = ids.find(
              (item) => item.id === preferredRequirementId,
            )?.id;
            const existing =
              dataRef.current?.document_id === id
                ? ids.find((item) => item.id === current)?.id
                : undefined;
            return (
              preferred ??
              existing ??
              ids.find((item) => statusOf(next, item.id) === "needs_attention")
                ?.id ??
              ids[0]?.id ??
              ""
            );
          });
        }
      } catch (reason) {
        if (!signal?.aborted) setError(errorText(reason));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(
      documentId,
      controller.signal,
      requestedId === documentId ? params.get("requirement") : null,
    );
    return () => controller.abort();
    // Fetch by document; requirement-query changes update selection below.
  }, [documentId, load]);
  useEffect(() => {
    const current = new URLSearchParams(params);
    if (documentId && current.get("document") !== documentId)
      current.set("document", documentId);
    if (!documentId) current.delete("document");
    if (current.toString() !== params.toString())
      setParams(current, { replace: true });
  }, [documentId, params, setParams]);
  useEffect(() => {
    const requirementId = params.get("requirement");
    if (requirementId === lastRequirementParam.current) return;
    lastRequirementParam.current = requirementId;
    if (data?.document_id !== documentId) return;
    const target = data.run?.extraction.requirements.find(
      (item) => item.id === requirementId,
    );
    if (!target) {
      setSelectedId("");
      setNote("");
      return;
    }
    setSelectedId(target.id);
    setNote(data.review?.decisions[target.id]?.note ?? "");
    const evidence = target.evidence.find(
      (item) => item.page >= 1 && item.page <= data.document.page_count,
    );
    if (evidence) setPage(evidence.page);
    // Apply requirement-query navigation within the loaded run.
  }, [params, data, documentId]);
  useEffect(() => {
    const option = workspace.providers.find((item) => item.id === provider);
    setModel(option?.model ?? "");
    setOutputLimit(Math.max(512, option?.output_limit ?? 4096));
    setPermission(false);
    setFreeTierConfirmed(false);
    // Provider changes reset consent; workspace refreshes keep the user's draft intact.
  }, [provider]);
  useEffect(() => {
    if (data?.document_id === documentId) setAnalysisOpen(!data.run);
  }, [data?.document_id, data?.run?.run_id, documentId]);

  const requirements = data?.run?.extraction.requirements ?? [];
  const selected: Requirement | undefined = requirements.find(
    (item) => item.id === selectedId,
  );
  useEffect(() => {
    setNote(data?.review?.decisions[selectedId]?.note ?? "");
    const evidence = selected?.evidence.find(
      (item) => item.page >= 1 && item.page <= (data?.document.page_count ?? 1),
    );
    setPage(evidence?.page ?? 1);
    // Selection changes initialize the note and citation page; refreshes keep the draft.
  }, [documentId, selectedId, data?.run?.run_id]);
  const actionableIds = useMemo(
    () =>
      requirements
        .filter((item) => statusOf(data!, item.id) === "needs_attention")
        .map((item) => item.id),
    [data, requirements],
  );
  const displayed =
    filter === "pending"
      ? requirements.filter((item) => actionableIds.includes(item.id))
      : requirements;
  const state = selected && data ? statusOf(data, selected.id) : "";
  const canReviewSelected =
    state === "needs_attention" ||
    (filter === "all" &&
      (state === "ready" || state === "confirmed" || state === "excluded"));
  const currentPage = Math.max(
    1,
    Math.min(data?.document.page_count ?? 1, page),
  );

  useEffect(() => {
    if (!documentId || !data || data.document_id !== documentId) {
      setPageImage("");
      return;
    }
    const controller = new AbortController();
    let objectUrl = "";
    const query = new URLSearchParams();
    if (selected && data.run) query.set("requirement_id", selected.id);
    if (data.run) query.set("run_id", data.run.run_id);
    setPageLoading(true);
    setPageError("");
    fetch(
      `/api/specifications/${encodeURIComponent(documentId)}/pages/${currentPage}?${query}`,
      { signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) {
          let message = "Unable to render this PDF page.";
          try {
            const body = await response.json();
            if (typeof body.detail === "string") message = body.detail;
          } catch {}
          throw new Error(message);
        }
        return response.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setPageImage(objectUrl);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setPageImage("");
          setPageError(errorText(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setPageLoading(false);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [currentPage, data, documentId, selected]);

  const selectRequirement = (id: string) => {
    setSelectedId(id);
    lastRequirementParam.current = id;
    setParams({ document: documentId, requirement: id });
    const item = requirements.find((requirement) => requirement.id === id);
    const first = item?.evidence.find(
      (evidence) =>
        evidence.page >= 1 && evidence.page <= (data?.document.page_count ?? 1),
    );
    if (first) setPage(first.page);
    setNote(data?.review?.decisions[id]?.note ?? "");
  };
  const advance = (
    nextData: SpecificationData,
    actedIds: string[],
    current: string,
  ) => {
    const ids =
      nextData.run?.extraction.requirements.map((item) => item.id) ?? [];
    const start = ids.indexOf(current);
    return (
      [...ids.slice(start + 1), ...ids.slice(0, Math.max(start, 0))].find(
        (id) =>
          !actedIds.includes(id) &&
          statusOf(nextData, id) === "needs_attention",
      ) ?? ""
    );
  };
  const review = async (
    decision: Decision,
    ids: string[],
    selectedNote: string,
    acknowledged: boolean,
  ) => {
    if (!data?.run || !data.review || !ids.length) return;
    if (decision === "excluded" && !selectedNote.trim()) {
      setError("Add a reason before excluding source records.");
      return;
    }
    if (ids.length > 1 && !acknowledged) {
      setError(
        "Acknowledge that you checked every selected record against the PDF.",
      );
      return;
    }
    setSaving(ids.length === 1);
    setBatchSaving(ids.length > 1);
    setError("");
    setBatchError("");
    const runId = data.run.run_id;
    const reviewVersion = data.review.version;
    const documentAtSubmit = documentId;
    try {
      const updated = await request<SpecificationData>(
        `/api/specifications/${encodeURIComponent(documentAtSubmit)}/review`,
        {
          method: "POST",
          body: JSON.stringify({
            run_id: runId,
            version: reviewVersion,
            requirement_ids: ids,
            decision,
            note: selectedNote.trim(),
            acknowledged,
          }),
        },
      );
      if (activeDocumentRef.current !== documentId) return;
      setData(updated);
      const next = advance(updated, ids, selectedId);
      if (ids.includes(selectedId)) {
        const nextId = next || selectedId;
        setSelectedId(nextId);
        lastRequirementParam.current = nextId;
        setParams({ document: documentAtSubmit, requirement: nextId });
        setNote(updated.review?.decisions[nextId]?.note ?? "");
        const evidencePage = updated.run?.extraction.requirements
          .find((item) => item.id === nextId)
          ?.evidence.find(
            (item) =>
              item.page >= 1 && item.page <= updated.document.page_count,
          )?.page;
        if (evidencePage) setPage(evidencePage);
      }
      setBatchIds([]);
      setBatchAck(false);
      setBatchNote("");
      onRefresh();
    } catch (reason) {
      const message =
        reason instanceof ApiError && reason.status === 409
          ? `${reason.message} Your draft is still here. Reload saved review only after deciding whether to keep it.`
          : errorText(reason);
      if (activeDocumentRef.current === documentAtSubmit)
        ids.length > 1 ? setBatchError(message) : setError(message);
    } finally {
      setSaving(false);
      setBatchSaving(false);
    }
  };

  const upload = async (file?: File) => {
    if (!file) return;
    setUploading(true);
    setUploadError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await request<{ document_id: string }>(
        "/api/specifications",
        { method: "POST", body: form },
      );
      onRefresh();
      lastRequirementParam.current = null;
      setParams({ document: result.document_id });
    } catch (reason) {
      setUploadError(errorText(reason));
    } finally {
      setUploading(false);
    }
  };
  const importDemo = async () => {
    setUploading(true);
    setUploadError("");
    try {
      const result = await request<{ document_id: string }>(
        "/api/specifications/demo",
        { method: "POST" },
      );
      onRefresh();
      lastRequirementParam.current = null;
      setParams({ document: result.document_id });
    } catch (reason) {
      setUploadError(errorText(reason));
    } finally {
      setUploading(false);
    }
  };
  const analyze = async () => {
    if (
      !permission ||
      (provider === "gemini" && !freeTierConfirmed) ||
      !model.trim() ||
      !data
    )
      return;
    const documentAtSubmit = documentId;
    setAnalyzing(true);
    setAnalysisError("");
    try {
      const focusedPasses = Math.ceil(data.document.page_count / 3);
      if (strategy === "focused_passes" && focusedPasses > 8) {
        setAnalysisError(
          `Focused passes would make ${focusedPasses} provider requests. This mode is limited to 8 passes for this document.`,
        );
        setAnalyzing(false);
        return;
      }
      const updated = await request<SpecificationData>(
        `/api/specifications/${encodeURIComponent(documentAtSubmit)}/analysis`,
        {
          method: "POST",
          body: JSON.stringify({
            provider,
            model: model.trim(),
            strategy,
            output_limit: outputLimit,
            permission,
            free_tier_confirmed:
              provider === "gemini" ? freeTierConfirmed : true,
          }),
        },
      );
      if (activeDocumentRef.current === documentAtSubmit) {
        const nextRequirement =
          updated.run?.extraction.requirements.find(
            (item) => statusOf(updated, item.id) === "needs_attention",
          )?.id ??
          updated.run?.extraction.requirements[0]?.id ??
          "";
        setData(updated);
        setSelectedId(nextRequirement);
        lastRequirementParam.current = nextRequirement || null;
        setParams(
          nextRequirement
            ? { document: documentAtSubmit, requirement: nextRequirement }
            : { document: documentAtSubmit },
        );
        setAnalysisOpen(false);
      }
      onRefresh();
    } catch (reason) {
      const message = errorText(reason);
      if (activeDocumentRef.current === documentAtSubmit) {
        setAnalysisError(
          `${message} Refreshing saved data while keeping the previous result visible.`,
        );
        try {
          const latest = await request<SpecificationData>(
            `/api/specifications/${encodeURIComponent(documentAtSubmit)}`,
          );
          if (activeDocumentRef.current === documentAtSubmit) setData(latest);
        } catch (refreshReason) {
          if (activeDocumentRef.current === documentAtSubmit)
            setAnalysisError(
              `${message} Saved data refresh also failed: ${errorText(refreshReason)}`,
            );
        }
      }
    } finally {
      setAnalyzing(false);
    }
  };

  const pdfUrl = documentId
    ? `/api/specifications/${encodeURIComponent(documentId)}/pdf`
    : "";
  const sourcePanel = (
    <Paper
      sx={{
        ...panelSx,
        p: { xs: 1.5, md: 2 },
        minWidth: 0,
        height: { lg: "calc(100vh - 210px)" },
        overflow: "auto",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
      >
        <Typography variant="subtitle1" fontWeight={700}>
          Source document
        </Typography>
        {pdfUrl && (
          <Button
            size="small"
            component="a"
            href={pdfUrl}
            target="_blank"
            rel="noreferrer"
          >
            Open PDF
          </Button>
        )}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="center"
        spacing={1}
        sx={{ my: 1.5 }}
      >
        <IconButton
          size="small"
          aria-label="Previous page"
          disabled={currentPage <= 1}
          onClick={() => setPage(currentPage - 1)}
        >
          <ChevronLeftIcon />
        </IconButton>
        <TextField
          size="small"
          type="number"
          label={`Page of ${data?.document.page_count ?? 1}`}
          value={currentPage}
          onChange={(event) =>
            setPage(
              Math.max(
                1,
                Math.min(
                  data?.document.page_count ?? 1,
                  Number(event.target.value) || 1,
                ),
              ),
            )
          }
          sx={{ width: 125 }}
          inputProps={{ min: 1, max: data?.document.page_count ?? 1 }}
        />
        <IconButton
          size="small"
          aria-label="Next page"
          disabled={currentPage >= (data?.document.page_count ?? 1)}
          onClick={() => setPage(currentPage + 1)}
        >
          <ChevronRightIcon />
        </IconButton>
        {selected && (
          <Button
            size="small"
            onClick={() => {
              const found = selected.evidence.find(
                (item) =>
                  item.page >= 1 &&
                  item.page <= (data?.document.page_count ?? 1),
              );
              if (found) setPage(found.page);
            }}
          >
            Relocate
          </Button>
        )}
      </Stack>
      {pageLoading && (
        <Box sx={{ display: "grid", placeItems: "center", minHeight: 200 }}>
          <CircularProgress size={28} />
        </Box>
      )}
      {pageError && <Alert severity="warning">{pageError}</Alert>}
      {pageImage && (
        <Box
          component="img"
          src={pageImage}
          alt={`PDF page ${currentPage}${selected ? ", selected evidence highlighted when located" : ""}`}
          sx={{
            display: pageLoading ? "none" : "block",
            width: "100%",
            height: "auto",
            maxHeight: compact ? "65vh" : "calc(100vh - 270px)",
            objectFit: "contain",
            bgcolor: "#eef1f6",
            borderRadius: 1,
          }}
        />
      )}
      {selected && (
        <Typography
          variant="caption"
          color="text.secondary"
          display="block"
          sx={{ mt: 1 }}
        >
          Evidence highlighting is a locator. Check the quotation against the
          original PDF.
        </Typography>
      )}
    </Paper>
  );

  const detail = (
    <>
      {selected ? (
        <>
          <Stack
            direction="row"
            alignItems="flex-start"
            justifyContent="space-between"
            gap={1}
          >
            <Box>
              <Typography
                variant="h6"
                fontWeight={700}
                sx={{ lineHeight: 1.25 }}
              >
                {selected.original_test_name}
              </Typography>
              {selected.requested_methods.length > 0 && (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.6 }}
                >
                  {selected.requested_methods
                    .map(
                      (method) =>
                        `${method.code}${method.revision ? ` (${method.revision})` : ""}${method.role ? ` — ${method.role}` : ""}`,
                    )
                    .join(" · ")}
                </Typography>
              )}
            </Box>
            {states[state] && (
              <Chip
                size="small"
                label={states[state].label}
                color={states[state].color}
              />
            )}
          </Stack>
          {data?.assessments[selected.id]?.reasons?.length ? (
            <Alert severity="warning" sx={{ mt: 1.5 }}>
              Review flagged: {data.assessments[selected.id].reasons.join("; ")}
            </Alert>
          ) : null}
          <Divider sx={{ my: 2 }} />
          {(
            [
              ["Conditions", selected.conditions],
              ["Acceptance criteria", selected.acceptance_criteria],
            ] as const
          ).map(([heading, values]) => (
            <Box key={heading} sx={{ mb: 2 }}>
              <Typography
                variant="subtitle2"
                fontWeight={700}
                sx={{ mb: 0.75 }}
              >
                {heading}
              </Typography>
              {values.length ? (
                <Stack spacing={0.75}>
                  {values.map((item, index) => (
                    <Box
                      key={`${item.name}-${index}`}
                      sx={{ p: 1.25, bgcolor: "#f7f9fc", borderRadius: 1.5 }}
                    >
                      {item.name &&
                        !["Customer condition", "Customer criterion"].includes(
                          item.name,
                        ) && (
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            display="block"
                          >
                            {item.name}
                          </Typography>
                        )}
                      <Typography variant="body2">{valueText(item)}</Typography>
                      {item.context && (
                        <Typography variant="caption" color="text.secondary">
                          {item.context}
                        </Typography>
                      )}
                    </Box>
                  ))}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  Not specified
                </Typography>
              )}
            </Box>
          ))}
          {selected.applicability.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 0.5 }}>
                Applicability
              </Typography>
              {selected.applicability.map((text, i) => (
                <Typography key={i} variant="body2">
                  • {text}
                </Typography>
              ))}
            </Box>
          )}
          {selected.special_instructions.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 0.5 }}>
                Special instructions / alternatives
              </Typography>
              {selected.special_instructions.map((text, i) => (
                <Typography key={i} variant="body2">
                  • {text}
                </Typography>
              ))}
            </Box>
          )}
          {selected.evidence.length > 0 && (
            <Accordion
              disableGutters
              elevation={0}
              sx={{
                border: "1px solid #e3e8f0",
                borderRadius: "10px !important",
                "&:before": { display: "none" },
              }}
            >
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography variant="body2" fontWeight={600}>
                  Source quotations & review note
                </Typography>
              </AccordionSummary>
              <AccordionDetails>
                <Stack spacing={1}>
                  {selected.evidence.map((evidence, index) => (
                    <Paper
                      variant="outlined"
                      key={index}
                      sx={{ p: 1.25, bgcolor: "#fbfcfe" }}
                    >
                      <Typography variant="caption" color="text.secondary">
                        Page {evidence.page}
                        {evidence.section ? ` · ${evidence.section}` : ""}
                      </Typography>
                      <Typography
                        variant="body2"
                        sx={{ whiteSpace: "pre-wrap" }}
                      >
                        {evidence.quote}
                      </Typography>
                    </Paper>
                  ))}
                  <TextField
                    multiline
                    minRows={2}
                    maxRows={5}
                    fullWidth
                    label="Review note / corrections to resolve"
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    inputProps={{ maxLength: 4000 }}
                  />
                </Stack>
              </AccordionDetails>
            </Accordion>
          )}
          {selected.evidence.length === 0 && (
            <TextField
              multiline
              minRows={2}
              fullWidth
              label="Review note / corrections to resolve"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              inputProps={{ maxLength: 4000 }}
            />
          )}
        </>
      ) : (
        <Box sx={{ py: 5, textAlign: "center" }}>
          <Typography color="text.secondary">
            Select a requirement to review its extracted details.
          </Typography>
        </Box>
      )}
    </>
  );

  return (
    <Box
      sx={{
        minHeight: "100%",
        bgcolor: pageBg,
        p: { xs: 1.5, sm: 2.5, lg: 3 },
        color: "#17243b",
      }}
    >
      <Stack spacing={2.25} sx={{ maxWidth: 1600, mx: "auto" }}>
        <Box>
          <Typography
            variant="h4"
            fontWeight={750}
            sx={{ letterSpacing: "-.025em", fontSize: { xs: 26, sm: 32 } }}
          >
            Specifications
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5, maxWidth: 850 }}>
            Check that the extracted tests, conditions and criteria match the
            customer PDF. Confirming extraction does not assess whether your lab
            can perform a test.
          </Typography>
        </Box>

        {!docs.length && (
          <Paper sx={{ ...panelSx, p: { xs: 2, sm: 4 }, textAlign: "center" }}>
            <UploadFileOutlinedIcon sx={{ fontSize: 40, color: blue, mb: 1 }} />
            <Typography variant="h6" fontWeight={700}>
              Save a specification PDF
            </Typography>
            <Typography color="text.secondary" sx={{ mb: 2 }}>
              Import is local. No model runs until you explicitly request
              analysis.
            </Typography>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1.25}
              justifyContent="center"
            >
              <Button
                component="label"
                variant="contained"
                startIcon={<UploadFileOutlinedIcon />}
                disabled={uploading}
              >
                {uploading ? "Saving…" : "Choose PDF"}
                <input
                  hidden
                  type="file"
                  accept="application/pdf,.pdf"
                  onChange={(event) => {
                    void upload(event.target.files?.[0]);
                    event.currentTarget.value = "";
                  }}
                />
              </Button>
              <Button
                variant="outlined"
                onClick={() => void importDemo()}
                disabled={uploading}
              >
                Import demo specification
              </Button>
            </Stack>
            {uploadError && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {uploadError}
              </Alert>
            )}
            <Typography
              variant="caption"
              color="text.secondary"
              display="block"
              sx={{ mt: 2 }}
            >
              Text PDFs · up to 25 MB / 200 pages
            </Typography>
          </Paper>
        )}

        {docs.length > 0 && (
          <Stack
            direction={{ xs: "column", md: "row" }}
            spacing={1.5}
            alignItems={{ md: "center" }}
          >
            <FormControl
              size="small"
              sx={{ minWidth: { xs: "100%", md: 310 }, bgcolor: "#fff" }}
            >
              <InputLabel id="spec-doc-label">Saved specification</InputLabel>
              <Select
                labelId="spec-doc-label"
                label="Saved specification"
                value={documentId}
                onChange={(event) => {
                  setParams({ document: event.target.value });
                  setSelectedId("");
                  setError("");
                }}
              >
                {docs.map((doc) => (
                  <MenuItem value={doc.document_id} key={doc.document_id}>
                    {doc.original_name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <Stack direction="row" spacing={1}>
              <Button
                component="label"
                variant="outlined"
                startIcon={<UploadFileOutlinedIcon />}
                disabled={uploading}
              >
                {uploading ? "Saving…" : "Add PDF"}
                <input
                  hidden
                  type="file"
                  accept="application/pdf,.pdf"
                  onChange={(event) => {
                    void upload(event.target.files?.[0]);
                    event.currentTarget.value = "";
                  }}
                />
              </Button>
              <Button
                variant="outlined"
                onClick={() => void importDemo()}
                disabled={uploading}
              >
                Import demo
              </Button>
            </Stack>
            {uploadError && <Alert severity="error">{uploadError}</Alert>}
          </Stack>
        )}

        {error && (
          <Alert severity="error" onClose={() => setError("")}>
            {error}
          </Alert>
        )}
        {loading && (
          <Paper
            sx={{ ...panelSx, p: 5, display: "grid", placeItems: "center" }}
          >
            <CircularProgress />
          </Paper>
        )}
        {!loading && data && (
          <>
            {data.document.warnings.map((warning, index) => (
              <Alert severity="warning" key={index}>
                {warning}
              </Alert>
            ))}
            <Accordion
              expanded={analysisOpen}
              onChange={(_, expanded) => setAnalysisOpen(expanded)}
              disableGutters
              sx={{ ...panelSx, "&:before": { display: "none" } }}
            >
              <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: 2 }}>
                <Box>
                  <Typography variant="subtitle1" fontWeight={700}>
                    {data.run
                      ? "Analysis settings"
                      : "Analyze this specification"}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Analysis is manual. Previous extracted content stays
                    available if a new request fails.
                  </Typography>
                </Box>
              </AccordionSummary>
              <AccordionDetails sx={{ pt: 0 }}>
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: {
                      xs: "1fr",
                      lg: "minmax(0,1fr) minmax(280px, auto)",
                    },
                    gap: 2,
                    alignItems: "start",
                  }}
                >
                  <Box>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      spacing={1.25}
                      sx={{ mt: 1.5 }}
                    >
                      <FormControl size="small" sx={{ minWidth: 160 }}>
                        <InputLabel id="provider-label">Provider</InputLabel>
                        <Select
                          labelId="provider-label"
                          label="Provider"
                          value={provider}
                          onChange={(event) => setProvider(event.target.value)}
                        >
                          {workspace.providers.map((item) => (
                            <MenuItem
                              key={item.id}
                              value={item.id}
                              disabled={!item.available}
                            >
                              {item.label}
                              {!item.available ? " · unavailable" : ""}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                      <FormControl size="small" sx={{ minWidth: 200, flex: 1 }}>
                        <InputLabel id="strategy-label">
                          Reading mode
                        </InputLabel>
                        <Select
                          labelId="strategy-label"
                          label="Reading mode"
                          value={strategy}
                          onChange={(event) =>
                            setStrategy(event.target.value as typeof strategy)
                          }
                        >
                          <MenuItem value="compact_document">
                            Compact · one request
                          </MenuItem>
                          <MenuItem value="whole_document">
                            Detailed · one request
                          </MenuItem>
                          <MenuItem value="focused_passes">
                            Focused coverage passes
                          </MenuItem>
                        </Select>
                      </FormControl>
                    </Stack>
                    <Accordion
                      disableGutters
                      elevation={0}
                      sx={{
                        mt: 0.5,
                        bgcolor: "transparent",
                        "&:before": { display: "none" },
                      }}
                    >
                      <AccordionSummary
                        expandIcon={<ExpandMoreIcon />}
                        sx={{ px: 0, minHeight: 40 }}
                      >
                        <Typography variant="body2" color="primary">
                          Advanced model settings
                        </Typography>
                      </AccordionSummary>
                      <AccordionDetails sx={{ px: 0 }}>
                        <Stack
                          direction={{ xs: "column", sm: "row" }}
                          spacing={1.25}
                        >
                          <TextField
                            size="small"
                            label="Model"
                            value={model}
                            onChange={(event) => setModel(event.target.value)}
                            fullWidth
                          />
                          <TextField
                            size="small"
                            type="number"
                            label="Max output tokens per request"
                            value={outputLimit}
                            onChange={(event) =>
                              setOutputLimit(
                                Math.max(
                                  512,
                                  Number(event.target.value) || 512,
                                ),
                              )
                            }
                            sx={{ width: { sm: 240 } }}
                            inputProps={{ min: 512, step: 128 }}
                          />
                        </Stack>
                      </AccordionDetails>
                    </Accordion>
                  </Box>
                  <Stack spacing={0.25} sx={{ minWidth: { lg: 300 } }}>
                    <FormControlLabel
                      control={
                        <Checkbox
                          checked={permission}
                          onChange={(event) =>
                            setPermission(event.target.checked)
                          }
                        />
                      }
                      label={
                        <Typography variant="body2">
                          I have permission to send this document to{" "}
                          {selectedProvider?.label ?? "the selected provider"}
                        </Typography>
                      }
                    />
                    {provider === "gemini" && (
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={freeTierConfirmed}
                            onChange={(event) =>
                              setFreeTierConfirmed(event.target.checked)
                            }
                          />
                        }
                        label={
                          <Typography variant="body2">
                            I confirmed this Google project uses the Free tier
                          </Typography>
                        }
                      />
                    )}
                    <Button
                      variant="contained"
                      onClick={() => void analyze()}
                      disabled={
                        analyzing ||
                        !permission ||
                        (provider === "gemini" && !freeTierConfirmed) ||
                        !model.trim() ||
                        !selectedProvider?.available ||
                        (strategy === "focused_passes" &&
                          Math.ceil(data.document.page_count / 3) > 8)
                      }
                      sx={{ alignSelf: { lg: "flex-start" }, bgcolor: blue }}
                    >
                      {analyzing
                        ? "Analyzing…"
                        : data.run
                          ? "Analyze again"
                          : "Analyze PDF"}
                    </Button>
                  </Stack>
                  {strategy === "focused_passes" && (
                    <Typography
                      variant="caption"
                      color={
                        Math.ceil(data.document.page_count / 3) > 8
                          ? "error.main"
                          : "text.secondary"
                      }
                      sx={{ gridColumn: "1/-1" }}
                    >
                      Focused passes would make{" "}
                      {Math.ceil(data.document.page_count / 3)} provider
                      requests (one for each 3-page focus). Each pass still
                      receives full document context; the limit is 8 passes per
                      analysis.
                    </Typography>
                  )}
                  {analysisError && (
                    <Alert severity="error" sx={{ gridColumn: "1/-1" }}>
                      {analysisError}
                    </Alert>
                  )}
                  {data.failure &&
                    (!data.run ||
                      Date.parse(data.failure.occurred_at) >
                        Date.parse(data.run.started_at)) && (
                      <Alert severity="warning" sx={{ gridColumn: "1/-1" }}>
                        The latest analysis attempt failed. Showing the saved
                        result from{" "}
                        {data.failure.previous_run_id
                          ? "the previous successful run"
                          : "before analysis"}
                        : {data.failure.message}
                      </Alert>
                    )}
                </Box>
              </AccordionDetails>
            </Accordion>

            {data.run && data.coverage_issues.length > 0 && (
              <Alert severity="warning">
                Coverage check: {data.coverage_issues.join(" · ")}
              </Alert>
            )}
            <Stack
              direction={compact ? "column" : "row"}
              spacing={2}
              alignItems="stretch"
              sx={{ minWidth: 0 }}
            >
              {!compact && (
                <Box sx={{ flex: "0 0 45%", minWidth: 0 }}>{sourcePanel}</Box>
              )}
              <Paper
                sx={{
                  ...panelSx,
                  p: { xs: 1.25, sm: 1.75 },
                  width: compact ? "100%" : undefined,
                  flex: compact ? undefined : 1,
                  minWidth: 0,
                  display: "flex",
                  flexDirection: "column",
                  maxHeight: compact ? "none" : "calc(100vh - 210px)",
                }}
              >
                {data.run ? (
                  <>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      alignItems={{ sm: "center" }}
                      justifyContent="space-between"
                      gap={1}
                    >
                      <Box>
                        <Typography variant="subtitle1" fontWeight={700}>
                          Test requirements
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {requirements.length} extracted ·{" "}
                          {actionableIds.length} need your review
                        </Typography>
                      </Box>
                      <Stack direction="row" spacing={0.5}>
                        <Button
                          size="small"
                          variant={filter === "pending" ? "contained" : "text"}
                          onClick={() => setFilter("pending")}
                        >
                          Pending
                        </Button>
                        <Button
                          size="small"
                          variant={filter === "all" ? "contained" : "text"}
                          onClick={() => setFilter("all")}
                        >
                          All
                        </Button>
                      </Stack>
                    </Stack>
                    <Accordion
                      disableGutters
                      elevation={0}
                      sx={{ mt: 0.5, "&:before": { display: "none" } }}
                    >
                      <AccordionSummary
                        expandIcon={<ExpandMoreIcon />}
                        sx={{ minHeight: 42 }}
                      >
                        <Typography variant="body2" fontWeight={650}>
                          {filter === "pending"
                            ? `Needs review · ${actionableIds.length}`
                            : `All extracted tests · ${requirements.length}`}
                        </Typography>
                      </AccordionSummary>
                      <AccordionDetails
                        sx={{ pt: 0, maxHeight: 210, overflowY: "auto" }}
                      >
                        <Stack spacing={0.5}>
                          {displayed.map((item) => {
                            const itemStatus =
                              states[statusOf(data, item.id)] ?? states.ready;
                            return (
                              <Button
                                key={item.id}
                                onClick={() => selectRequirement(item.id)}
                                variant="text"
                                sx={{
                                  textTransform: "none",
                                  color: "#17243b",
                                  textAlign: "left",
                                  justifyContent: "space-between",
                                  gap: 1,
                                  border: "1px solid",
                                  borderColor:
                                    selectedId === item.id ? blue : "#e6eaf1",
                                  borderRadius: 1.5,
                                  p: 1,
                                  bgcolor:
                                    selectedId === item.id ? "#f3f7ff" : "#fff",
                                  "&:hover": { bgcolor: "#f5f8fe" },
                                }}
                              >
                                <Typography
                                  variant="body2"
                                  fontWeight={
                                    selectedId === item.id ? 650 : 500
                                  }
                                  sx={{ overflowWrap: "anywhere", flex: 1 }}
                                >
                                  {item.original_test_name}
                                </Typography>
                                <Chip
                                  size="small"
                                  label={itemStatus.label}
                                  color={itemStatus.color}
                                  sx={{ flexShrink: 0 }}
                                />
                              </Button>
                            );
                          })}
                          {!displayed.length && (
                            <Alert
                              severity={
                                filter === "pending" ? "success" : "info"
                              }
                            >
                              {filter === "pending"
                                ? "No flagged items are waiting for review. Unflagged records remain available under All."
                                : "No extracted requirements are available."}
                            </Alert>
                          )}
                        </Stack>
                      </AccordionDetails>
                    </Accordion>
                    {requirements.length > 0 && (
                      <Accordion
                        disableGutters
                        elevation={0}
                        sx={{ "&:before": { display: "none" } }}
                      >
                        <AccordionSummary
                          expandIcon={<ExpandMoreIcon />}
                          sx={{ minHeight: 42 }}
                        >
                          <Typography variant="body2" fontWeight={650}>
                            Batch review
                          </Typography>
                        </AccordionSummary>
                        <AccordionDetails>
                          <Stack spacing={1}>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              Choose each record explicitly and check it against
                              the PDF before saving.
                            </Typography>
                            <Box sx={{ maxHeight: 180, overflowY: "auto" }}>
                              {requirements.map((item) => (
                                <FormControlLabel
                                  key={item.id}
                                  control={
                                    <Checkbox
                                      size="small"
                                      checked={batchIds.includes(item.id)}
                                      onChange={(event) => {
                                        setBatchIds((current) =>
                                          event.target.checked
                                            ? [...current, item.id]
                                            : current.filter(
                                                (id) => id !== item.id,
                                              ),
                                        );
                                        setBatchAck(false);
                                      }}
                                    />
                                  }
                                  label={
                                    <Typography variant="body2">
                                      {item.original_test_name}
                                    </Typography>
                                  }
                                />
                              ))}
                            </Box>
                            <TextField
                              size="small"
                              multiline
                              minRows={2}
                              label="Batch note / exclusion reason"
                              value={batchNote}
                              onChange={(event) =>
                                setBatchNote(event.target.value)
                              }
                            />
                            {batchIds.length > 1 && (
                              <FormControlLabel
                                control={
                                  <Checkbox
                                    checked={batchAck}
                                    onChange={(event) =>
                                      setBatchAck(event.target.checked)
                                    }
                                  />
                                }
                                label={
                                  <Typography variant="body2">
                                    I checked every selected record against the
                                    PDF
                                  </Typography>
                                }
                              />
                            )}
                            {batchError && (
                              <Alert severity="error">{batchError}</Alert>
                            )}
                            <Stack
                              direction={{ xs: "column", sm: "row" }}
                              spacing={1}
                            >
                              <Button
                                variant="contained"
                                disabled={
                                  !batchIds.length ||
                                  batchSaving ||
                                  (batchIds.length > 1 && !batchAck)
                                }
                                onClick={() =>
                                  void review(
                                    "confirmed",
                                    batchIds,
                                    batchNote,
                                    batchAck,
                                  )
                                }
                              >
                                {batchSaving ? "Saving…" : "Confirm selected"}
                              </Button>
                              <Button
                                variant="outlined"
                                disabled={
                                  !batchIds.length ||
                                  batchSaving ||
                                  !batchNote.trim() ||
                                  (batchIds.length > 1 && !batchAck)
                                }
                                onClick={() =>
                                  void review(
                                    "excluded",
                                    batchIds,
                                    batchNote,
                                    batchAck,
                                  )
                                }
                              >
                                Exclude selected
                              </Button>
                            </Stack>
                          </Stack>
                        </AccordionDetails>
                      </Accordion>
                    )}
                    <Divider sx={{ my: 1 }} />
                    <Box
                      sx={{
                        p: { xs: 0.25, sm: 0.75 },
                        overflowY: "auto",
                        minHeight: 140,
                        flex: 1,
                        maxHeight: compact ? "none" : "calc(100vh - 465px)",
                      }}
                    >
                      {detail}
                    </Box>
                    <Divider sx={{ mt: 1 }} />
                    <Box
                      sx={{
                        p: 1.25,
                        bgcolor: "#fff",
                        position: "sticky",
                        bottom: 0,
                        zIndex: 1,
                      }}
                    >
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        display="block"
                        sx={{ mb: 0.75 }}
                      >
                        {state === "confirmed"
                          ? "Confirmed by you."
                          : state === "excluded"
                            ? "Excluded from this request."
                            : state === "needs_attention"
                              ? "Check this extraction against the original PDF."
                              : "No automatic review flags. You may still review this item under All."}
                      </Typography>
                      {error && (
                        <Alert
                          severity="error"
                          sx={{ mb: 1 }}
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
                      <Stack
                        direction={{ xs: "column", sm: "row" }}
                        spacing={1}
                      >
                        <Button
                          fullWidth
                          variant="contained"
                          disabled={
                            !selected ||
                            !canReviewSelected ||
                            saving ||
                            batchSaving ||
                            !data.review
                          }
                          onClick={() =>
                            void review("confirmed", [selectedId], note, false)
                          }
                          sx={{ bgcolor: blue, whiteSpace: "nowrap" }}
                        >
                          {saving ? "Saving…" : "Confirm & next"}
                        </Button>
                        <Button
                          fullWidth
                          variant="outlined"
                          disabled={
                            !selected ||
                            !canReviewSelected ||
                            saving ||
                            batchSaving ||
                            !data.review ||
                            !note.trim()
                          }
                          onClick={() =>
                            void review("excluded", [selectedId], note, false)
                          }
                          sx={{ whiteSpace: "nowrap" }}
                        >
                          Exclude & next
                        </Button>
                      </Stack>
                      {!note.trim() &&
                        state !== "confirmed" &&
                        state !== "excluded" && (
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ mt: 0.5, display: "block" }}
                          >
                            Add a reason in the source quotation section before
                            excluding.
                          </Typography>
                        )}
                    </Box>
                  </>
                ) : (
                  <Box sx={{ py: 3, px: 1 }}>
                    <Typography variant="subtitle1" fontWeight={700}>
                      No extraction yet
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Your PDF is saved. No model request has been made. Open
                      Analysis settings above when you are ready to analyze it.
                    </Typography>
                  </Box>
                )}
              </Paper>
              {compact && (
                <Accordion sx={{ ...panelSx, "&:before": { display: "none" } }}>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Typography fontWeight={700}>
                      Source PDF preview · page {currentPage}
                    </Typography>
                  </AccordionSummary>
                  <AccordionDetails sx={{ p: 1.5 }}>
                    {sourcePanel}
                  </AccordionDetails>
                </Accordion>
              )}
            </Stack>
            {data.run && (
              <Stack
                direction={{ xs: "column", sm: "row" }}
                alignItems={{ sm: "center" }}
                spacing={1.5}
              >
                <Button
                  variant="contained"
                  endIcon={<ArrowForwardIcon />}
                  onClick={() =>
                    navigate(
                      `/reports?document=${encodeURIComponent(documentId)}`,
                    )
                  }
                  sx={{ bgcolor: blue, alignSelf: "flex-start" }}
                >
                  Continue to lab review
                </Button>
                <Typography variant="body2" color="text.secondary">
                  Next, review test matching and lab support in Reports.
                  Unfinished source checks remain flagged.
                </Typography>
              </Stack>
            )}
          </>
        )}
      </Stack>
    </Box>
  );
}
