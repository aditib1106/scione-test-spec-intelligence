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
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
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
  Tooltip,
  useMediaQuery,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import UploadFileOutlinedIcon from "@mui/icons-material/UploadFileOutlined";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import DocumentScannerOutlinedIcon from "@mui/icons-material/DocumentScannerOutlined";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import DeleteOutlineRoundedIcon from "@mui/icons-material/DeleteOutlineRounded";
import MoreHorizRoundedIcon from "@mui/icons-material/MoreHorizRounded";
import SourcePageReference from "../components/SourcePageReference";
import { ApiError, request } from "../api";
import PdfPageViewer from "../components/PdfPageViewer";
import SourceFactsEditor from "../components/SourceFactsEditor";
import ExtractionReviewTable, {
  type SourceField,
} from "../components/ExtractionReviewTable";
import InferenceProgress from "../components/InferenceProgress";
import DocumentRevisionNotice from "../components/DocumentRevisionNotice";
import {
  claimContext,
  claimValue,
  isReviewParameter,
  isSamplePlanClaim,
} from "../components/sourceReviewClaims";
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
  ready: { label: "Ready to compare", color: "success" },
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
function previewIdentity(
  documentId: string,
  data: SpecificationData | null,
  requirementId: string,
  page: number,
  field: SourceField | null,
  claimId?: string | null,
) {
  return JSON.stringify([
    documentId,
    data?.run?.run_id ?? "",
    data?.facts_version ?? 0,
    requirementId,
    page,
    field,
    claimId ?? null,
  ]);
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
  const [filter, setFilter] = useState<Filter>(
    params.get("requirement") ? "all" : "pending",
  );
  const [selectedId, setSelectedId] = useState(params.get("requirement") ?? "");
  const [page, setPage] = useState(1);
  const [pageImage, setPageImage] = useState("");
  const [pageImageKey, setPageImageKey] = useState("");
  const [pageError, setPageError] = useState("");
  const [pageLoading, setPageLoading] = useState(false);
  const [sourceField, setSourceField] = useState<SourceField | null>(null);
  const [sourceClaimId, setSourceClaimId] = useState<string | null>(null);
  const [editClaimRequest, setEditClaimRequest] = useState<{
    claimId: string;
    nonce: number;
  } | null>(null);
  const [highlightScope, setHighlightScope] = useState("none");
  const [highlightFocus, setHighlightFocus] = useState<number | null>(null);
  const [sourceLocateRequest, setSourceLocateRequest] = useState<
    number | undefined
  >(undefined);
  const sourceLocateSequence = useRef(0);
  const [sourceOpen, setSourceOpen] = useState(false);
  const sourcePreviewRef = useRef<HTMLDivElement>(null);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeReason, setRemoveReason] = useState("Not a requested test");
  const [removeNote, setRemoveNote] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisDocumentName, setAnalysisDocumentName] = useState("");
  const analysisSubmitting = useRef(false);
  const analysisMounted = useRef(true);
  const analysisController = useRef<AbortController | null>(null);
  useEffect(() => {
    analysisMounted.current = true;
    return () => {
      analysisMounted.current = false;
      analysisController.current?.abort();
    };
  }, []);
  const [provider, setProvider] = useState(
    workspace.providers.find((item) => item.available)?.id ??
      workspace.providers[0]?.id ??
      "",
  );
  const selectedProvider = workspace.providers.find(
    (item) => item.id === provider,
  );
  const requiresUsageAcknowledgment = ["gemini", "fireworks", "sail"].includes(
    provider,
  );
  const [model, setModel] = useState(selectedProvider?.model ?? "");
  const [strategy, setStrategy] = useState<
    | "inventory_compact_document"
    | "compact_document"
    | "whole_document"
    | "focused_passes"
  >("inventory_compact_document");
  const [outputLimit, setOutputLimit] = useState(
    selectedProvider?.output_limit ?? 4096,
  );
  const [permission, setPermission] = useState(false);
  const [freeTierConfirmed, setFreeTierConfirmed] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [batchNote, setBatchNote] = useState("");
  const [batchAck, setBatchAck] = useState(false);
  const [batchSaving, setBatchSaving] = useState(false);
  const [batchError, setBatchError] = useState("");
  const [analysisOpen, setAnalysisOpen] = useState(!data?.run);
  const requestedSourcePage = params.get("page");

  useEffect(() => {
    setBatchOpen(false);
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
        if (!signal?.aborted && activeDocumentRef.current === id) {
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
        if (!signal?.aborted && activeDocumentRef.current === id)
          setError(errorText(reason));
      } finally {
        if (!signal?.aborted && activeDocumentRef.current === id)
          setLoading(false);
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
    setFilter("all");
    setNote(data.review?.decisions[target.id]?.note ?? "");
    const evidence = target.evidence.find(
      (item) => item.page >= 1 && item.page <= data.document.page_count,
    );
    const sourcePage = Number(params.get("page"));
    if (
      Number.isInteger(sourcePage) &&
      sourcePage >= 1 &&
      sourcePage <= data.document.page_count
    )
      setPage(sourcePage);
    else if (evidence) setPage(evidence.page);
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
  const displayTestName = (item: Requirement) =>
    item.original_test_name.length > 60 && !item.id.startsWith("req_human_")
      ? data?.normalized_requirements?.[item.id]?.display_test_name ||
        item.original_test_name
      : item.original_test_name;
  const selected: Requirement | undefined = requirements.find(
    (item) => item.id === selectedId,
  );
  useEffect(() => {
    setSourceField(null);
    setSourceClaimId(null);
    setSourceLocateRequest(undefined);
  }, [
    documentId,
    selectedId,
    data?.run?.run_id,
    data?.facts_version,
    requestedSourcePage,
  ]);
  useEffect(() => {
    if (data?.run && selectedId)
      setSourceLocateRequest(++sourceLocateSequence.current);
  }, [documentId, selectedId, data?.run?.run_id, requestedSourcePage]);
  useEffect(() => {
    setNote(data?.review?.decisions[selectedId]?.note ?? "");
    const evidence = selected?.evidence.find(
      (item) => item.page >= 1 && item.page <= (data?.document.page_count ?? 1),
    );
    const sourcePage =
      params.get("requirement") === selectedId
        ? Number(requestedSourcePage)
        : 0;
    setPage(
      Number.isInteger(sourcePage) &&
        sourcePage >= 1 &&
        sourcePage <= (data?.document.page_count ?? 0)
        ? sourcePage
        : (evidence?.page ?? 1),
    );
    // Selection changes initialize the note and citation page; refreshes keep the draft.
  }, [documentId, selectedId, data?.run?.run_id, requestedSourcePage]);
  const actionableIds = useMemo(
    () =>
      requirements
        .filter((item) => statusOf(data!, item.id) === "needs_attention")
        .map((item) => item.id),
    [data, requirements],
  );
  useEffect(() => {
    if (data?.run && actionableIds.length === 0) setFilter("all");
  }, [data?.run?.run_id, actionableIds.length]);
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
  const currentPreviewKey = previewIdentity(
    documentId,
    data,
    selected?.id ?? "",
    currentPage,
    sourceField,
    sourceClaimId,
  );
  const activePreviewKey = useRef(currentPreviewKey);
  activePreviewKey.current = currentPreviewKey;

  useEffect(() => {
    if (!documentId || !data || data.document_id !== documentId) {
      setPageImage("");
      setPageImageKey("");
      return;
    }
    const controller = new AbortController();
    let objectUrl = "";
    let responseFocus: number | null = null;
    let responseScope = "none";
    const requestedPreviewKey = currentPreviewKey;
    const query = new URLSearchParams();
    if (selected && data.run) query.set("requirement_id", selected.id);
    if (data.run) query.set("run_id", data.run.run_id);
    if (sourceField) query.set("field", sourceField);
    if (sourceClaimId) query.set("claim_id", sourceClaimId);
    setPageLoading(true);
    setPageError("");
    setHighlightFocus(null);
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
        responseScope = response.headers.get("X-Highlight-Scope") ?? "item";
        const focus = response.headers.get("X-Highlight-Focus");
        const normalized = focus === null ? Number.NaN : Number(focus);
        responseFocus =
          Number.isFinite(normalized) && normalized >= 0 && normalized <= 1
            ? normalized
            : null;
        return response.blob();
      })
      .then((blob) => {
        if (
          controller.signal.aborted ||
          activePreviewKey.current !== requestedPreviewKey
        )
          return;
        objectUrl = URL.createObjectURL(blob);
        setHighlightScope(responseScope);
        setHighlightFocus(responseFocus);
        if (responseFocus === null) setSourceLocateRequest(undefined);
        setPageImageKey(requestedPreviewKey);
        setPageImage(objectUrl);
      })
      .catch((reason) => {
        if (
          !controller.signal.aborted &&
          activePreviewKey.current === requestedPreviewKey
        ) {
          setPageImage("");
          setPageImageKey("");
          setSourceLocateRequest(undefined);
          setPageError(errorText(reason));
        }
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          activePreviewKey.current === requestedPreviewKey
        )
          setPageLoading(false);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // Source runs are immutable. Saving review decisions does not change the
    // highlighted source image and must not reload it or shift nearby actions.
  }, [
    currentPage,
    data?.document_id,
    data?.run?.run_id,
    documentId,
    selected?.id,
    sourceField,
    sourceClaimId,
    currentPreviewKey,
  ]);

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
    if (
      !data?.run ||
      !data.review ||
      !ids.length ||
      data.document_id !== documentId
    )
      return;
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
            facts_version: data.facts_version ?? 0,
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
      return true;
    } catch (reason) {
      const message =
        reason instanceof ApiError && reason.status === 409
          ? `${reason.message} Your draft is still here. Reload saved review only after deciding whether to keep it.`
          : errorText(reason);
      if (activeDocumentRef.current === documentAtSubmit)
        ids.length > 1 ? setBatchError(message) : setError(message);
      return false;
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
      analysisSubmitting.current ||
      !permission ||
      (requiresUsageAcknowledgment && !freeTierConfirmed) ||
      !model.trim() ||
      !data ||
      data.document_id !== documentId
    )
      return;
    const documentAtSubmit = documentId;
    analysisSubmitting.current = true;
    const controller = new AbortController();
    analysisController.current = controller;
    setAnalyzing(true);
    setAnalysisDocumentName(data.original_name);
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
          signal: controller.signal,
          body: JSON.stringify({
            provider,
            model: model.trim(),
            strategy,
            output_limit: outputLimit,
            permission,
            billing_acknowledged: requiresUsageAcknowledgment
              ? freeTierConfirmed
              : true,
          }),
        },
      );
      if (!analysisMounted.current || controller.signal.aborted) return;
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
      if (!analysisMounted.current || controller.signal.aborted) return;
      const message = errorText(reason);
      if (activeDocumentRef.current === documentAtSubmit) {
        setAnalysisError(
          `${message} Refreshing saved data while keeping the previous result visible.`,
        );
        try {
          const latest = await request<SpecificationData>(
            `/api/specifications/${encodeURIComponent(documentAtSubmit)}`,
            { signal: controller.signal },
          );
          if (
            analysisMounted.current &&
            activeDocumentRef.current === documentAtSubmit
          )
            setData(latest);
        } catch (refreshReason) {
          if (
            analysisMounted.current &&
            !controller.signal.aborted &&
            activeDocumentRef.current === documentAtSubmit
          )
            setAnalysisError(
              `${message} Saved data refresh also failed: ${errorText(refreshReason)}`,
            );
        }
      }
    } finally {
      analysisSubmitting.current = false;
      if (analysisController.current === controller)
        analysisController.current = null;
      if (analysisMounted.current) {
        setAnalyzing(false);
        setPermission(false);
        setFreeTierConfirmed(false);
      }
    }
  };

  const pdfUrl = documentId
    ? `/api/specifications/${encodeURIComponent(documentId)}/pdf`
    : "";
  const sourceNavigation = (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <IconButton
        size="small"
        aria-label="Previous page"
        disabled={currentPage <= 1}
        onClick={() => {
          setSourceLocateRequest(undefined);
          setPage(currentPage - 1);
        }}
      >
        <ChevronLeftIcon />
      </IconButton>
      <TextField
        size="small"
        type="number"
        label={`Page of ${data?.document.page_count ?? 1}`}
        value={currentPage}
        onChange={(event) => {
          setSourceLocateRequest(undefined);
          setPage(
            Math.max(
              1,
              Math.min(
                data?.document.page_count ?? 1,
                Number(event.target.value) || 1,
              ),
            ),
          );
        }}
        sx={{ width: 115 }}
        inputProps={{ min: 1, max: data?.document.page_count ?? 1 }}
      />
      <IconButton
        size="small"
        aria-label="Next page"
        disabled={currentPage >= (data?.document.page_count ?? 1)}
        onClick={() => {
          setSourceLocateRequest(undefined);
          setPage(currentPage + 1);
        }}
      >
        <ChevronRightIcon />
      </IconButton>
      {selected && (
        <Button
          size="small"
          onClick={() => {
            const linkedPage = Number(requestedSourcePage);
            if (
              params.get("requirement") === selected.id &&
              Number.isInteger(linkedPage) &&
              linkedPage >= 1 &&
              linkedPage <= (data?.document.page_count ?? 1) &&
              selected.evidence.some((item) => item.page === linkedPage)
            ) {
              locateSource(sourceField, linkedPage, sourceClaimId ?? undefined);
              return;
            }
            const found = selected.evidence.find(
              (item) =>
                item.page >= 1 && item.page <= (data?.document.page_count ?? 1),
            );
            if (found)
              locateSource(sourceField, found.page, sourceClaimId ?? undefined);
          }}
        >
          Relocate
        </Button>
      )}
    </Stack>
  );
  const sourcePanel = (
    <Paper
      sx={{
        ...panelSx,
        p: { xs: 1.25, md: 1.5 },
        minWidth: 0,
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <PdfPageViewer
        title="Source document"
        imageUrl={pageImage}
        alt={`PDF page ${currentPage}${selected ? ", selected evidence highlighted when located" : ""}`}
        loading={pageLoading}
        error={pageError}
        documentUrl={pdfUrl}
        navigation={sourceNavigation}
        height={compact ? "72vh" : "100%"}
        focusY={highlightFocus}
        focusRequest={
          pageImageKey === currentPreviewKey ? sourceLocateRequest : undefined
        }
      />
      {selected && (
        <Typography
          variant="caption"
          color="text.secondary"
          display="block"
          sx={{ mt: 1 }}
        >
          {pageLoading
            ? "Locating the source…"
            : highlightScope === "none"
              ? "No exact passage located on this page. Use a View source link or open the original PDF."
              : highlightScope === "parameter"
                ? "Highlighted: the selected parameter’s source."
                : sourceField && highlightScope === "field"
                  ? "Highlighted: the selected field’s source."
                  : sourceField
                    ? "Showing the test’s quoted source; an exact field location is unavailable."
                    : "Yellow highlights show the selected test’s quoted source where located."}
        </Typography>
      )}
    </Paper>
  );

  const locateSource = (
    field: SourceField | null,
    sourcePage: number,
    claimId?: string,
  ) => {
    const targetKey = previewIdentity(
      documentId,
      data,
      selected?.id ?? "",
      sourcePage,
      field,
      claimId,
    );
    const knownNoFocus =
      targetKey === pageImageKey && !pageLoading && highlightFocus === null;
    if (field !== sourceField || sourcePage !== currentPage)
      setHighlightFocus(null);
    setSourceField(field);
    setSourceClaimId(claimId ?? null);
    setPage(sourcePage);
    setSourceLocateRequest(
      knownNoFocus ? undefined : ++sourceLocateSequence.current,
    );
    if (compact) {
      setSourceOpen(true);
      window.requestAnimationFrame(() =>
        sourcePreviewRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        }),
      );
    }
  };
  const blankRequirement: Requirement = {
    id: "req_manual_draft",
    original_test_name: "",
    requested_methods: [],
    conditions: [],
    acceptance_criteria: [],
    applicability: [],
    special_instructions: [],
    atomic_claims: [],
    evidence: [{ page: currentPage, quote: "" }],
  };
  const onAdded = (updated: SpecificationData) => {
    if (
      activeDocumentRef.current !== documentId ||
      updated.document_id !== documentId
    )
      return;
    const added = updated.run?.extraction.requirements.find(
      (item) => !requirements.some((existing) => existing.id === item.id),
    );
    setData(updated);
    setFilter("all");
    if (added) {
      setSelectedId(added.id);
      lastRequirementParam.current = added.id;
      setParams({ document: documentId, requirement: added.id });
    }
    onRefresh();
  };
  const reviewReasons = selected
    ? (data?.assessments[selected.id]?.reasons ?? [])
    : [];
  const sourceNotices = selected
    ? (data?.assessments[selected.id]?.notices ?? [])
    : [];
  const planningClaims = selected
    ? (
        data?.normalized_requirements?.[selected.id]?.claims ??
        selected.atomic_claims ??
        []
      ).filter(isSamplePlanClaim)
    : [];
  const selectedStatusChip =
    selected && states[state] ? (
      <Chip
        size="small"
        label={
          state === "ready" && selected.id.startsWith("req_human_")
            ? "Added by you"
            : states[state].label
        }
        color={states[state].color}
      />
    ) : null;
  const detail = selected ? (
    <Stack spacing={1.75}>
      {state === "needs_attention" && (
        <Box
          sx={{
            p: 1.25,
            borderLeft: "3px solid #cc861e",
            bgcolor: "#fff8e9",
            borderRadius: 1,
          }}
        >
          <Typography variant="body2" fontWeight={650}>
            Check this item against the PDF
          </Typography>
          {reviewReasons.length > 0 && (
            <Accordion
              disableGutters
              elevation={0}
              sx={{ bgcolor: "transparent", "&:before": { display: "none" } }}
            >
              <AccordionSummary
                expandIcon={<ExpandMoreIcon />}
                sx={{
                  p: 0,
                  minHeight: 32,
                  "& .MuiAccordionSummary-content": { my: 0.5 },
                }}
              >
                <Typography variant="caption" fontWeight={600}>
                  Why this needs review
                </Typography>
              </AccordionSummary>
              <AccordionDetails sx={{ p: 0 }}>
                <Stack gap={0.75}>
                  {reviewReasons.map((reason, i) => (
                    <Typography
                      key={i}
                      variant="caption"
                      color="text.secondary"
                      sx={{ overflowWrap: "anywhere" }}
                    >
                      {reason.replace(/\breq_[A-Za-z0-9_-]+\b[: ]*/g, "")}
                    </Typography>
                  ))}
                </Stack>
              </AccordionDetails>
            </Accordion>
          )}
        </Box>
      )}
      <ExtractionReviewTable
        requirement={selected}
        claims={data?.normalized_requirements?.[selected.id]?.claims}
        displayName={displayTestName(selected)}
        sourceField={sourceField}
        sourceClaimId={sourceClaimId}
        onSource={locateSource}
        onEditClaim={(claimId) =>
          setEditClaimRequest({ claimId, nonce: Date.now() })
        }
      />
      {sourceNotices.length > 0 && (
        <Accordion
          disableGutters
          elevation={0}
          sx={{
            border: "1px solid #dae4f3",
            borderRadius: "8px !important",
            bgcolor: "#f7faff",
            "&:before": { display: "none" },
          }}
        >
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="body2" fontWeight={600}>
              Alternative method notes
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Stack gap={1}>
              {sourceNotices.map((notice, index) => (
                <Typography
                  key={index}
                  variant="body2"
                  sx={{ lineHeight: 1.65 }}
                >
                  {notice.replace(/\breq_[A-Za-z0-9_-]+\b[: ]*/g, "")}
                </Typography>
              ))}
            </Stack>
          </AccordionDetails>
        </Accordion>
      )}
      {(selected.applicability.length > 0 ||
        selected.special_instructions.length > 0 ||
        planningClaims.length > 0) && (
        <Accordion
          disableGutters
          elevation={0}
          sx={{
            border: "1px solid #e2e8f0",
            borderRadius: "10px !important",
            "&:before": { display: "none" },
          }}
        >
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="body2" fontWeight={600}>
              Sample plan & other instructions
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Stack gap={1.5}>
              {planningClaims.map((claim) => (
                <Box
                  key={claim.id}
                  sx={{
                    display: "grid",
                    gridTemplateColumns:
                      "minmax(110px, 0.8fr) minmax(0, 1.2fr)",
                    gap: 1.5,
                    pb: 1.25,
                    borderBottom: "1px solid #e5ebf3",
                  }}
                >
                  <Typography variant="body2" fontWeight={600}>
                    {claim.label}
                  </Typography>
                  <Box>
                    <Typography
                      variant="body2"
                      sx={{ overflowWrap: "anywhere" }}
                    >
                      {claimValue(claim)}
                    </Typography>
                    {claimContext(claim) && (
                      <Typography variant="caption" color="text.secondary">
                        {claimContext(claim)}
                      </Typography>
                    )}
                    {claim.source_pages.map((sourcePage) => (
                      <SourcePageReference
                        key={sourcePage}
                        page={sourcePage}
                        label={`View sample plan source on page ${sourcePage} for ${claim.label}`}
                        onClick={() => locateSource(null, sourcePage, claim.id)}
                      />
                    ))}
                    {isReviewParameter(claim) && (
                      <Button
                        size="small"
                        aria-label={`Edit sample plan ${claim.label}`}
                        onClick={() =>
                          setEditClaimRequest({
                            claimId: claim.id,
                            nonce: Date.now(),
                          })
                        }
                        sx={{ p: 0, mt: 0.4, fontSize: 11 }}
                      >
                        Edit
                      </Button>
                    )}
                  </Box>
                </Box>
              ))}
              {[...selected.applicability, ...selected.special_instructions]
                .length > 0 && (
                <Accordion
                  disableGutters
                  elevation={0}
                  sx={{ "&:before": { display: "none" } }}
                >
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Typography variant="caption" fontWeight={600}>
                      Original instruction wording
                    </Typography>
                  </AccordionSummary>
                  <AccordionDetails>
                    <Stack gap={1}>
                      {[
                        ...selected.applicability,
                        ...selected.special_instructions,
                      ].map((text, index) => (
                        <Typography
                          key={index}
                          variant="body2"
                          sx={{ lineHeight: 1.65 }}
                        >
                          {text}
                        </Typography>
                      ))}
                    </Stack>
                  </AccordionDetails>
                </Accordion>
              )}
            </Stack>
          </AccordionDetails>
        </Accordion>
      )}
      <Accordion
        disableGutters
        elevation={0}
        sx={{
          border: "1px solid #e2e8f0",
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
          <Stack spacing={1.25}>
            {selected.evidence.map((evidence, index) => (
              <Box
                key={index}
                sx={{ p: 1.25, bgcolor: "#f6f8fb", borderRadius: 1 }}
              >
                <Button
                  size="small"
                  onClick={() => {
                    locateSource(null, evidence.page);
                  }}
                >
                  Page {evidence.page}
                </Button>
                <Typography
                  variant="body2"
                  sx={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}
                >
                  {evidence.quote}
                </Typography>
              </Box>
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
    </Stack>
  ) : (
    <Box sx={{ py: 5, textAlign: "center" }}>
      <Typography color="text.secondary">
        Select a test to see what was extracted.
      </Typography>
    </Box>
  );

  const documentControls = docs.length > 0 && (
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
        <Tooltip title="Scanned and handwritten PDFs will be supported here when OCR is connected.">
          <span>
            <Button
              disabled
              startIcon={<DocumentScannerOutlinedIcon />}
              sx={{
                color: "#566476 !important",
                bgcolor: "#e6eaf0 !important",
                border: "1px solid #d2d9e3",
              }}
            >
              OCR · coming soon
            </Button>
          </span>
        </Tooltip>
      </Stack>
      {uploadError && <Alert severity="error">{uploadError}</Alert>}
    </Stack>
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
      <Stack spacing={1.5} sx={{ maxWidth: 1800, mx: "auto" }}>
        <Stack
          direction={{ xs: "column", lg: "row" }}
          alignItems={{ lg: "center" }}
          justifyContent="space-between"
          spacing={1.5}
        >
          <Box>
            <Typography
              variant="h5"
              fontWeight={750}
              sx={{ letterSpacing: "-.025em" }}
            >
              PDF review
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Check extracted requirements against their highlighted source.
            </Typography>
          </Box>
          {documentControls}
        </Stack>

        {!docs.length && (
          <Paper sx={{ ...panelSx, p: { xs: 2, sm: 4 }, textAlign: "center" }}>
            <UploadFileOutlinedIcon sx={{ fontSize: 40, color: blue, mb: 1 }} />
            <Typography variant="h6" fontWeight={700}>
              Save a specification PDF
            </Typography>
            <Typography color="text.secondary" sx={{ mb: 2 }}>
              Add a PDF, then extract and review its test requirements.
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
              <Tooltip title="Scanned and handwritten PDFs will be supported here when OCR is connected.">
                <span>
                  <Button
                    disabled
                    startIcon={<DocumentScannerOutlinedIcon />}
                    sx={{
                      color: "#566476 !important",
                      bgcolor: "#e6eaf0 !important",
                    }}
                  >
                    OCR · coming soon
                  </Button>
                </span>
              </Tooltip>
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
        {!loading && data && data.document_id === documentId && (
          <>
            {data.run?.provider === "manual_reference" && (
              <Alert severity="info" sx={{ py: 0 }}>
                Manually prepared example requirements. Analyze the PDF to
                evaluate a model.
              </Alert>
            )}
            {data.document.warnings.map((warning, index) => (
              <Alert severity="warning" key={index}>
                {warning}
              </Alert>
            ))}
            <DocumentRevisionNotice issues={data.document_review_issues} />
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
                  {analysisOpen && (
                    <Typography variant="body2" color="text.secondary">
                      Previous extracted content stays available if a new
                      request fails.
                    </Typography>
                  )}
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
                      <FormControl
                        size="small"
                        sx={{ minWidth: 160 }}
                        disabled={analyzing}
                      >
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
                      <FormControl
                        size="small"
                        sx={{ minWidth: 200, flex: 1 }}
                        disabled={analyzing}
                      >
                        <InputLabel id="strategy-label">
                          Reading mode
                        </InputLabel>
                        <Select
                          labelId="strategy-label"
                          label="Reading mode"
                          value={strategy}
                          onChange={(event) => {
                            setStrategy(event.target.value as typeof strategy);
                            setPermission(false);
                            setFreeTierConfirmed(false);
                          }}
                        >
                          <MenuItem value="inventory_compact_document">
                            Inventory + extraction · two requests
                          </MenuItem>
                          <MenuItem value="compact_document">
                            Compact · complete document
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
                            disabled={analyzing}
                            onChange={(event) => {
                              setModel(event.target.value);
                              setPermission(false);
                              setFreeTierConfirmed(false);
                            }}
                            fullWidth
                          />
                          <TextField
                            size="small"
                            type="number"
                            label="Max output tokens per request"
                            value={outputLimit}
                            disabled={analyzing}
                            onChange={(event) => {
                              setOutputLimit(
                                Math.max(
                                  512,
                                  Number(event.target.value) || 512,
                                ),
                              );
                              setPermission(false);
                              setFreeTierConfirmed(false);
                            }}
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
                          disabled={analyzing}
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
                    {requiresUsageAcknowledgment && (
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={freeTierConfirmed}
                            disabled={analyzing}
                            onChange={(event) =>
                              setFreeTierConfirmed(event.target.checked)
                            }
                          />
                        }
                        label={
                          <Typography variant="body2">
                            {provider === "gemini"
                              ? "I understand this uses my Google project's API quota and may incur charges"
                              : `I understand this uses my ${selectedProvider?.label ?? "provider"} API quota or credits and may incur charges`}
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
                        (requiresUsageAcknowledgment && !freeTierConfirmed) ||
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
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ gridColumn: "1/-1" }}
                  >
                    Analysis starts only when you press the button. Failed
                    requests are not retried automatically. Leaving this page
                    stops waiting, but a request already accepted by the model
                    provider may finish and use credits.
                  </Typography>
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
                  {data.failure?.attempt_id && (
                    <Accordion
                      disableGutters
                      elevation={0}
                      sx={{
                        gridColumn: "1/-1",
                        bgcolor: "transparent",
                        "&:before": { display: "none" },
                      }}
                    >
                      <AccordionSummary
                        expandIcon={<ExpandMoreIcon />}
                        sx={{ px: 0 }}
                      >
                        <Typography variant="caption" color="text.secondary">
                          Developer details · failed analysis
                        </Typography>
                      </AccordionSummary>
                      <AccordionDetails sx={{ px: 0 }}>
                        <Button
                          size="small"
                          component="a"
                          href={`/api/specifications/${encodeURIComponent(documentId)}/analysis-failures/${encodeURIComponent(data.failure.attempt_id)}/artifacts`}
                          download
                        >
                          Download failed attempt
                        </Button>
                        <Typography
                          variant="caption"
                          display="block"
                          color="text.secondary"
                        >
                          Includes source prompts, original model output and
                          reported usage. Review before explicitly starting
                          another analysis.
                        </Typography>
                      </AccordionDetails>
                    </Accordion>
                  )}
                </Box>
              </AccordionDetails>
            </Accordion>

            {analyzing && (
              <InferenceProgress
                title="Document analysis in progress"
                detail={`${analysisDocumentName} · Extracting test requirements and checking the output. This may take a few minutes.`}
              />
            )}

            {data.run && data.coverage_issues.length > 0 && (
              <Alert severity="warning">
                <Typography variant="body2" fontWeight={650}>
                  Some source information may be missing
                </Typography>
                <Typography variant="body2">
                  Check the PDF. Edit the scope or instructions, or add a
                  missing test.
                </Typography>
                <Accordion
                  disableGutters
                  elevation={0}
                  sx={{
                    bgcolor: "transparent",
                    "&:before": { display: "none" },
                  }}
                >
                  <AccordionSummary
                    expandIcon={<ExpandMoreIcon />}
                    sx={{ p: 0, minHeight: 32 }}
                  >
                    <Typography variant="caption">
                      See coverage notes
                    </Typography>
                  </AccordionSummary>
                  <AccordionDetails sx={{ p: 0 }}>
                    <Typography variant="caption">
                      {data.coverage_issues.join(" · ")}
                    </Typography>
                  </AccordionDetails>
                </Accordion>
              </Alert>
            )}
            <Stack
              direction={compact ? "column" : "row"}
              spacing={2}
              alignItems="stretch"
              sx={{
                minWidth: 0,
                height: compact
                  ? "auto"
                  : "clamp(680px, calc(100dvh - 250px), 980px)",
              }}
            >
              {!compact && (
                <Box sx={{ flex: "0 0 49%", minWidth: 0, minHeight: 0 }}>
                  {sourcePanel}
                </Box>
              )}
              {compact && (
                <Accordion
                  ref={sourcePreviewRef}
                  expanded={sourceOpen}
                  onChange={(_, expanded) => setSourceOpen(expanded)}
                  sx={{
                    ...panelSx,
                    scrollMarginTop: 70,
                    "&:before": { display: "none" },
                  }}
                >
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
              <Paper
                sx={{
                  ...panelSx,
                  p: { xs: 1.25, sm: 1.75 },
                  width: compact ? "100%" : undefined,
                  flex: compact ? undefined : 1,
                  minWidth: 0,
                  display: "flex",
                  flexDirection: "column",
                  height: compact ? "auto" : "100%",
                  minHeight: compact ? 0 : 680,
                  overflow: "hidden",
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
                          {requirements.length} tests · {actionableIds.length}{" "}
                          {actionableIds.length === 1 ? "needs" : "need"} your
                          review
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
                        {requirements.length > 1 && (
                          <Tooltip title="Review several tests">
                            <IconButton
                              size="small"
                              aria-label="Batch review"
                              onClick={() => {
                                setBatchOpen(true);
                                setBatchError("");
                              }}
                            >
                              <MoreHorizRoundedIcon />
                            </IconButton>
                          </Tooltip>
                        )}
                      </Stack>
                    </Stack>
                    <FormControl fullWidth size="small" sx={{ mt: 1.5 }}>
                      <InputLabel id="selected-test-label">
                        Selected test
                      </InputLabel>
                      <Select
                        labelId="selected-test-label"
                        label="Selected test"
                        value={selectedId}
                        onChange={(event) =>
                          selectRequirement(event.target.value)
                        }
                        disabled={!requirements.length}
                      >
                        {[
                          ...displayed,
                          ...(selected &&
                          !displayed.some((item) => item.id === selected.id)
                            ? [selected]
                            : []),
                        ].map((item) => (
                          <MenuItem key={item.id} value={item.id}>
                            <Typography
                              variant="body2"
                              sx={{ whiteSpace: "normal" }}
                            >
                              {displayTestName(item)}
                            </Typography>
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <Box
                      sx={{
                        mt: 1,
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      {selectedStatusChip}
                      <SourceFactsEditor
                        key={`add-${documentId}`}
                        data={data}
                        selected={blankRequirement}
                        adding
                        onSaved={onAdded}
                      />
                    </Box>
                    <Divider sx={{ my: 1 }} />
                    <Box
                      sx={{
                        flex: 1,
                        minHeight: 0,
                        overflowY: compact ? "visible" : "auto",
                        scrollbarGutter: "stable",
                        overflowAnchor: "none",
                      }}
                    >
                      <Box
                        sx={{
                          p: { xs: 0.25, sm: 0.75 },
                          minHeight: 140,
                        }}
                      >
                        {detail}
                      </Box>
                    </Box>
                    <Divider sx={{ mt: 1 }} />
                    <Box
                      sx={{
                        p: 1.25,
                        bgcolor: "#fff",
                        flexShrink: 0,
                      }}
                    >
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        display="block"
                        sx={{ mb: 1 }}
                      >
                        {state === "confirmed"
                          ? "Checked against the PDF."
                          : state === "excluded"
                            ? "Removed from this request. The original is kept."
                            : state === "ready"
                              ? "Ready for comparison. Reviewing this item is optional."
                              : "Check this extraction against the PDF. Lab capability is compared in the report."}
                      </Typography>
                      {error && (
                        <Alert severity="error" sx={{ mb: 1 }}>
                          {error}
                        </Alert>
                      )}
                      <Stack
                        direction="row"
                        spacing={0.75}
                        alignItems="center"
                        flexWrap="wrap"
                        useFlexGap
                      >
                        {state !== "confirmed" && state !== "excluded" && (
                          <Button
                            variant={
                              state === "needs_attention"
                                ? "contained"
                                : "outlined"
                            }
                            startIcon={<CheckRoundedIcon />}
                            disabled={
                              !selected ||
                              !canReviewSelected ||
                              saving ||
                              batchSaving ||
                              !data.review
                            }
                            onClick={() =>
                              void review(
                                "confirmed",
                                [selectedId],
                                note,
                                false,
                              )
                            }
                            sx={{
                              bgcolor:
                                state === "needs_attention" ? blue : undefined,
                              flex: state === "needs_attention" ? 1 : undefined,
                              minWidth: 135,
                              whiteSpace: "nowrap",
                            }}
                          >
                            {saving ? "Saving…" : "Confirm & next"}
                          </Button>
                        )}
                        {selected && (
                          <SourceFactsEditor
                            key={`${documentId}-${selected.id}-${data.facts_version ?? 0}`}
                            data={data}
                            selected={selected}
                            editRequest={editClaimRequest}
                            onEditRequestHandled={() =>
                              setEditClaimRequest(null)
                            }
                            onSaved={(updated) => {
                              if (
                                activeDocumentRef.current !== documentId ||
                                updated.document_id !== documentId
                              )
                                return;
                              setEditClaimRequest(null);
                              setData(updated);
                              onRefresh();
                            }}
                          />
                        )}
                        <Button
                          variant="outlined"
                          startIcon={<DeleteOutlineRoundedIcon />}
                          disabled={
                            !selected || saving || batchSaving || !data.review
                          }
                          onClick={() => {
                            if (state === "excluded") {
                              void review(
                                "needs_review",
                                [selectedId],
                                "Restored for source review.",
                                false,
                              );
                            } else {
                              setRemoveReason("Not a requested test");
                              setRemoveNote("");
                              setRemoveOpen(true);
                            }
                          }}
                          sx={{
                            whiteSpace: "nowrap",
                            color:
                              state === "excluded" ? "primary.main" : "#ad3340",
                            borderColor: "#d6dce6",
                          }}
                        >
                          {state === "excluded" ? "Restore" : "Remove"}
                        </Button>
                      </Stack>
                      {selected &&
                        state !== "confirmed" &&
                        state !== "excluded" && (
                          <Button
                            size="small"
                            disabled={saving || batchSaving || !data.review}
                            onClick={() =>
                              void review(
                                "needs_review",
                                [selectedId],
                                note.trim() || "More source review needed.",
                                false,
                              )
                            }
                            sx={{ mt: 0.5, color: "text.secondary" }}
                          >
                            Keep pending & next
                          </Button>
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
                  disabled={saving || batchSaving}
                  onClick={() =>
                    navigate(
                      `/reports?document=${encodeURIComponent(documentId)}`,
                      {
                        state: {
                          generateReportFor: documentId,
                          generationIntentId: crypto.randomUUID(),
                        },
                      },
                    )
                  }
                  sx={{ bgcolor: blue, alignSelf: "flex-start" }}
                >
                  Generate report
                </Button>
                <Typography variant="body2" color="text.secondary">
                  Compare these requirements with your saved lab information
                  using AI.
                </Typography>
              </Stack>
            )}
          </>
        )}
      </Stack>
      <Dialog
        open={removeOpen}
        onClose={() => {
          if (!saving) setRemoveOpen(false);
        }}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Remove this test?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            {selected?.original_test_name}
          </Typography>
          <FormControl fullWidth size="small">
            <InputLabel id="remove-reason-label">Reason</InputLabel>
            <Select
              labelId="remove-reason-label"
              label="Reason"
              value={removeReason}
              onChange={(event) => setRemoveReason(event.target.value)}
            >
              {[
                "Not a requested test",
                "Duplicate test",
                "Outside this request",
                "Other",
              ].map((reason) => (
                <MenuItem value={reason} key={reason}>
                  {reason}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField
            label="Additional note (optional)"
            fullWidth
            multiline
            minRows={2}
            value={removeNote}
            onChange={(event) => setRemoveNote(event.target.value)}
            sx={{ mt: 2 }}
            inputProps={{ maxLength: 3500 }}
          />
          <Typography
            variant="caption"
            color="text.secondary"
            display="block"
            sx={{ mt: 1.5 }}
          >
            Removes it from this request. The source PDF and original extraction
            are kept.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button disabled={saving} onClick={() => setRemoveOpen(false)}>
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            disabled={saving || !selected}
            onClick={async () => {
              const saved = await review(
                "excluded",
                [selectedId],
                [removeReason, removeNote.trim()].filter(Boolean).join(": "),
                false,
              );
              if (saved) setRemoveOpen(false);
            }}
          >
            {saving ? "Removing…" : "Remove test"}
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={batchOpen}
        onClose={() => {
          if (!saving && !batchSaving) setBatchOpen(false);
        }}
        fullWidth
        maxWidth="sm"
        fullScreen={compact}
        aria-labelledby="batch-review-heading"
      >
        <DialogTitle id="batch-review-heading">
          Review selected tests
        </DialogTitle>
        <DialogContent>
          {" "}
          <Stack spacing={1}>
            <Typography variant="caption" color="text.secondary">
              Choose each record explicitly and check it against the PDF before
              saving.
            </Typography>
            <Stack direction="row" gap={1}>
              <Button
                size="small"
                disabled={!actionableIds.length}
                onClick={() => {
                  setBatchIds(actionableIds);
                  setBatchAck(false);
                }}
              >
                Select pending
              </Button>
              <Button
                size="small"
                disabled={!batchIds.length}
                onClick={() => {
                  setBatchIds([]);
                  setBatchAck(false);
                }}
              >
                Clear selection
              </Button>
            </Stack>
            <Box
              sx={{
                maxHeight: 260,
                overflowY: "auto",
                display: "flex",
                flexDirection: "column",
              }}
            >
              {requirements.map((item) => (
                <FormControlLabel
                  key={item.id}
                  control={
                    <Checkbox
                      size="small"
                      inputProps={{ "aria-label": item.original_test_name }}
                      checked={batchIds.includes(item.id)}
                      onChange={(event) => {
                        setBatchIds((current) =>
                          event.target.checked
                            ? [...current, item.id]
                            : current.filter((id) => id !== item.id),
                        );
                        setBatchAck(false);
                      }}
                    />
                  }
                  label={
                    <Typography variant="body2">
                      {displayTestName(item)}
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
              onChange={(event) => setBatchNote(event.target.value)}
            />
            {batchIds.length > 1 && (
              <FormControlLabel
                control={
                  <Checkbox
                    checked={batchAck}
                    onChange={(event) => setBatchAck(event.target.checked)}
                  />
                }
                label={
                  <Typography variant="body2">
                    I checked every selected record against the PDF
                  </Typography>
                }
              />
            )}
            {(batchError || error) && (
              <Alert severity="error">{batchError || error}</Alert>
            )}
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
              <Button
                variant="contained"
                disabled={
                  !batchIds.length ||
                  batchSaving ||
                  (batchIds.length > 1 && !batchAck)
                }
                onClick={async () => {
                  if (await review("confirmed", batchIds, batchNote, batchAck))
                    setBatchOpen(false);
                }}
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
                onClick={async () => {
                  if (await review("excluded", batchIds, batchNote, batchAck))
                    setBatchOpen(false);
                }}
              >
                Exclude selected
              </Button>
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button
            disabled={saving || batchSaving}
            onClick={() => setBatchOpen(false)}
          >
            Close batch review
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
