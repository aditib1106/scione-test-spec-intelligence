import { useEffect, useMemo, useRef, useState } from "react";
import {
  Link as RouterLink,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormHelperText,
  InputAdornment,
  InputLabel,
  IconButton,
  List,
  ListItemButton,
  Menu,
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
  TablePagination,
  TableRow,
  Tabs,
  TextField,
  Typography,
  Tooltip,
} from "@mui/material";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import ArrowBackRoundedIcon from "@mui/icons-material/ArrowBackRounded";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import OpenInNewRoundedIcon from "@mui/icons-material/OpenInNewRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import UploadFileOutlinedIcon from "@mui/icons-material/UploadFileOutlined";
import DeleteOutlineRoundedIcon from "@mui/icons-material/DeleteOutlineRounded";
import AssessmentOutlinedIcon from "@mui/icons-material/AssessmentOutlined";
import MoreHorizRoundedIcon from "@mui/icons-material/MoreHorizRounded";
import HistoryRoundedIcon from "@mui/icons-material/HistoryRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import { ApiError, request, useResource } from "../api";
import { useAuth } from "../auth";
import {
  compareSpecificationDates,
  specificationVersionLabel,
} from "../specificationDates";
import FactSummary from "../components/FactSummary";
import DocumentRevisionNotice from "../components/DocumentRevisionNotice";
import type { PageProps, SpecificationUploadReceipt } from "../types";
import type {
  LibraryDetail,
  LibraryClaim,
  LibraryDocument,
  LibraryMetadata,
} from "../libraryTypes";

const emptyMetadata = (): LibraryMetadata => ({
  company_name: "",
  specification_id: "",
  title: "",
  revision: "",
  specification_date: null,
  family_id: null,
});

function documentTitle(document: LibraryDocument) {
  return document.title || document.specification_id || document.original_name;
}

function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "—"
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}

function stageLabel(value: string | null) {
  if (!value) return "Not specified";
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function stepLabel(claim: LibraryClaim, siblings: LibraryClaim[]) {
  if (!claim.sequence) return claim.configuration || "";
  const temperatures = [
    ...new Set(
      siblings
        .filter(
          (item) =>
            item.parameter_id === "temperature" &&
            item.stage === claim.stage &&
            item.sequence === claim.sequence &&
            (item.configuration ?? null) === (claim.configuration ?? null),
        )
        .map((item) => item.display_value),
    ),
  ];
  const temperature =
    claim.parameter_id !== "temperature" && temperatures.length === 1
      ? ` · ${temperatures[0]}`
      : "";
  return `Step ${claim.sequence}${temperature}${claim.configuration ? ` · ${claim.configuration}` : ""}`;
}

function requirementValue(value: string, unit: string | null) {
  return unit && value.endsWith(` ${unit}`)
    ? value.slice(0, -(unit.length + 1))
    : value;
}

function reviewUrl(
  documentId: string,
  requirementId?: string,
  page?: number | null,
) {
  const query = new URLSearchParams({ document: documentId });
  if (requirementId) query.set("requirement", requirementId);
  if (page) query.set("page", String(page));
  return `/specifications?${query}`;
}

export default function SpecificationLibrary({
  workspace,
  onRefresh,
}: PageProps) {
  const { canDevelop } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const library = useResource<{ documents: LibraryDocument[] }>("/api/library");
  const documents = library.data?.documents ?? [];
  const [query, setQuery] = useState("");
  const [company, setCompany] = useState("");
  const selectedId = params.get("document") || "";
  const detail = useResource<LibraryDetail>(
    selectedId ? `/api/library/${encodeURIComponent(selectedId)}` : null,
  );
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<LibraryDocument | null>(null);
  const [metadata, setMetadata] = useState<LibraryMetadata>(emptyMetadata);
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);
  const [uploadedId, setUploadedId] = useState<string | null>(null);
  const [uploadReceipt, setUploadReceipt] =
    useState<SpecificationUploadReceipt | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedTestId, setSelectedTestId] = useState("");
  const [detailTab, setDetailTab] = useState("tests");
  const [recordQuery, setRecordQuery] = useState("");
  const [recordPage, setRecordPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [removing, setRemoving] = useState<LibraryDocument | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState("");
  const [removeStale, setRemoveStale] = useState(false);
  const [specificationActions, setSpecificationActions] =
    useState<HTMLElement | null>(null);
  const [versionsFamilyId, setVersionsFamilyId] = useState<string | null>(null);

  useEffect(() => {
    library.refresh();
  }, [workspace.documents, library.refresh]);

  useEffect(() => {
    setSelectedTestId("");
    setDetailTab("tests");
    setRecordQuery("");
    setRecordPage(0);
    setSpecificationActions(null);
    setVersionsFamilyId(null);
  }, [selectedId]);

  useEffect(() => {
    setRecordPage(0);
  }, [selectedTestId, recordQuery, rowsPerPage]);

  const requirementRecords = useMemo(() => {
    const needle = recordQuery.trim().toLocaleLowerCase();
    return (detail.data?.requirements ?? [])
      .filter(
        (item) => !selectedTestId || item.requirement_id === selectedTestId,
      )
      .flatMap((item) =>
        item.claims.map((claim, index) => ({
          key: `${item.requirement_id}-${index}`,
          test: item,
          claim,
        })),
      )
      .filter(({ test, claim }) =>
        [test.test_name, claim.label, claim.display_value, claim.stage]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase()
          .includes(needle),
      );
  }, [detail.data, selectedTestId, recordQuery]);

  const totalRecordCount = (detail.data?.requirements ?? []).reduce(
    (total, item) => total + item.claims.length,
    0,
  );

  const versionGroups = useMemo(() => {
    const groups = new Map<string, LibraryDocument[]>();
    for (const document of documents) {
      const versions = groups.get(document.family_id) ?? [];
      versions.push(document);
      groups.set(document.family_id, versions);
    }
    for (const versions of groups.values())
      versions.sort(compareSpecificationDates);
    return groups;
  }, [documents]);
  const families = useMemo(
    () =>
      [...versionGroups.values()]
        .map((versions) => versions[0])
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [versionGroups],
  );
  const versionChoices = useMemo(() => {
    if (!versionsFamilyId) return [];
    const detailed =
      detail.data?.document.document_id === selectedId &&
      detail.data.document.family_id === versionsFamilyId
        ? detail.data.versions
        : undefined;
    return [...(detailed ?? versionGroups.get(versionsFamilyId) ?? [])]
      .filter((document) => document.family_id === versionsFamilyId)
      .sort(compareSpecificationDates);
  }, [versionsFamilyId, versionGroups, detail.data, selectedId]);
  const versionFamily = versionChoices[0];
  useEffect(() => {
    if (versionsFamilyId && !versionChoices.length) setVersionsFamilyId(null);
  }, [versionsFamilyId, versionChoices]);

  const companies = useMemo(
    () =>
      [...new Set(documents.map((item) => item.company_name).filter(Boolean))]
        .map(String)
        .sort((a, b) => a.localeCompare(b)),
    [documents],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return families.filter((document) => {
      const companyMatches =
        !company ||
        (company === "__unassigned__"
          ? !document.company_name
          : document.company_name === company);
      return (
        companyMatches &&
        (versionGroups.get(document.family_id) ?? [document]).some((version) =>
          [
            version.company_name,
            version.specification_id,
            version.title,
            version.original_name,
            version.revision,
            version.specification_date,
            specificationVersionLabel(version),
          ]
            .filter(Boolean)
            .join(" ")
            .toLocaleLowerCase()
            .includes(needle),
        )
      );
    });
  }, [families, versionGroups, query, company]);

  const current = detail.data?.document;
  const selectedTest =
    detail.data?.requirements.find(
      (item) => item.requirement_id === selectedTestId,
    ) || detail.data?.requirements[0];
  const selectedFamily =
    current?.family_id ||
    documents.find((document) => document.document_id === selectedId)
      ?.family_id;

  const openAdd = () => {
    setEditing(null);
    setMetadata(emptyMetadata());
    setFile(null);
    setUploadedId(null);
    setUploadReceipt(null);
    setSaveError("");
    setDialogOpen(true);
  };

  const openEdit = (document: LibraryDocument) => {
    setEditing(document);
    setMetadata({
      company_name: document.company_name ?? "",
      specification_id: document.specification_id ?? "",
      title: document.title ?? "",
      revision: document.revision ?? "",
      specification_date: document.specification_date ?? null,
      family_id: document.family_id,
    });
    setFile(null);
    setUploadedId(null);
    setUploadReceipt(null);
    setSaveError("");
    setDialogOpen(true);
  };

  const chooseFamily = (familyId: string) => {
    const family = families.find((item) => item.family_id === familyId);
    setMetadata((previous) => ({
      ...previous,
      family_id: familyId || null,
      ...(family
        ? {
            company_name: family.company_name ?? "",
            specification_id: family.specification_id ?? "",
            title: family.title ?? "",
          }
        : {}),
    }));
  };

  const save = async () => {
    if (submitting.current) return;
    setSaveError("");
    if (!editing && !uploadedId && !file) {
      setSaveError("Choose the specification PDF first.");
      return;
    }
    if (file && file.size > 25 * 1024 * 1024) {
      setSaveError("Choose a PDF smaller than 25 MB.");
      return;
    }
    submitting.current = true;
    setSaving(true);
    try {
      let documentId = editing?.document_id || uploadedId;
      let receipt = uploadReceipt;
      if (!documentId && file) {
        const body = new FormData();
        body.append("file", file);
        const uploaded = await request<SpecificationUploadReceipt>(
          "/api/specifications",
          { method: "POST", body },
        );
        documentId = uploaded.document_id;
        receipt = uploaded;
        setUploadReceipt(uploaded);
        setUploadedId(documentId);
        onRefresh();
      }
      if (!documentId) throw new Error("Choose the specification PDF first.");
      const saved = await request<LibraryDetail>(
        `/api/library/${encodeURIComponent(documentId)}`,
      );
      if (!editing && (receipt?.reused || saved.document.version > 0)) {
        // Preserve existing company/version metadata when the saved content is reopened.
        setDialogOpen(false);
        library.refresh();
        onRefresh();
        navigate(reviewUrl(documentId), { state: { uploadReceipt: receipt } });
        return;
      }
      await request(`/api/library/${encodeURIComponent(documentId)}/metadata`, {
        method: "PUT",
        body: JSON.stringify({
          ...metadata,
          // An unassigned PDF may already have been analyzed in PDF & review.
          // Leaving Add fields blank should keep those known source details.
          specification_id: editing
            ? metadata.specification_id
            : metadata.specification_id ||
              saved.document.specification_id ||
              "",
          title: editing
            ? metadata.title
            : metadata.title || saved.document.title || "",
          revision: editing
            ? metadata.revision
            : metadata.revision || saved.document.revision || "",
          specification_date: metadata.specification_date || null,
          expected_version: editing?.version ?? saved.document.version,
        }),
      });
      setDialogOpen(false);
      library.refresh();
      detail.refresh();
      onRefresh();
      if (editing) {
        setNotice("Specification details saved.");
      } else {
        navigate(reviewUrl(documentId), { state: { uploadReceipt: receipt } });
      }
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : "The specification could not be saved. Please try again.",
      );
    } finally {
      submitting.current = false;
      setSaving(false);
    }
  };

  const removeSpecification = async () => {
    if (!removing || removeBusy) return;
    setRemoveBusy(true);
    setRemoveError("");
    try {
      await request(
        `/api/library/${encodeURIComponent(removing.document_id)}`,
        {
          method: "DELETE",
          body: JSON.stringify({ version: removing.version }),
        },
      );
      if (selectedId === removing.document_id) setParams({});
      setNotice("Specification removed from your library.");
      setRemoving(null);
      library.refresh();
      onRefresh();
    } catch (reason) {
      setRemoveStale(reason instanceof ApiError && reason.status === 409);
      setRemoveError(
        reason instanceof Error
          ? reason.message
          : "Unable to remove this specification.",
      );
    } finally {
      setRemoveBusy(false);
    }
  };

  return (
    <Box sx={{ maxWidth: 1520, mx: "auto", px: { xs: 2, md: 4 }, py: 3 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        spacing={2}
        sx={{ mb: 3 }}
      >
        <Box>
          {selectedId && (
            <Button
              startIcon={<ArrowBackRoundedIcon />}
              onClick={() => setParams({})}
              sx={{ px: 0, mb: 0.5 }}
            >
              All specifications
            </Button>
          )}
          <Typography variant="h4">
            {selectedId ? "Specification details" : "Specification library"}
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            {selectedId
              ? "Browse the saved tests and requirements for this specification."
              : "Customer specifications, extracted tests and saved versions."}
          </Typography>
        </Box>
        {!selectedId && (
          <Stack
            direction="row"
            spacing={1}
            sx={{ flexShrink: 0, alignSelf: { xs: "flex-start", sm: "auto" } }}
          >
            <Button
              variant="contained"
              startIcon={<AddRoundedIcon />}
              onClick={openAdd}
            >
              Add specification
            </Button>
          </Stack>
        )}
      </Stack>

      {notice && (
        <Alert severity="success" onClose={() => setNotice("")} sx={{ mb: 2 }}>
          {notice}
        </Alert>
      )}
      {library.error && (
        <Alert
          severity="error"
          action={<Button onClick={library.refresh}>Retry</Button>}
          sx={{ mb: 2 }}
        >
          {library.error}
        </Alert>
      )}

      {!selectedId && (
        <Paper variant="outlined" sx={{ overflow: "hidden", mb: 3 }}>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.5}
            alignItems={{ sm: "center" }}
            sx={{ p: 2 }}
          >
            <TextField
              placeholder="Search company, specification or file"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              inputProps={{ "aria-label": "Search specification library" }}
              sx={{ flex: 1 }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchRoundedIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
            />
            <FormControl size="small" sx={{ minWidth: 190 }}>
              <InputLabel id="library-company-label">
                Customer company
              </InputLabel>
              <Select
                labelId="library-company-label"
                label="Customer company"
                value={company}
                onChange={(event) => setCompany(event.target.value)}
              >
                <MenuItem value="">All companies</MenuItem>
                {companies.map((name) => (
                  <MenuItem value={name} key={name}>
                    {name}
                  </MenuItem>
                ))}
                <MenuItem value="__unassigned__">Not assigned yet</MenuItem>
              </Select>
            </FormControl>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ whiteSpace: "nowrap" }}
            >
              {families.length} specification{families.length === 1 ? "" : "s"}
              {" · "}
              {documents.length} saved version
              {documents.length === 1 ? "" : "s"}
            </Typography>
          </Stack>

          {library.loading && !library.data ? (
            <Box sx={{ py: 6, textAlign: "center" }}>
              <CircularProgress size={24} />
            </Box>
          ) : documents.length === 0 ? (
            <Box
              sx={{
                px: 3,
                py: 6,
                textAlign: "center",
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <DescriptionOutlinedIcon
                sx={{ fontSize: 38, color: "#8b9bb2", mb: 1 }}
              />
              <Typography variant="h6">
                Your customer specifications, in one place
              </Typography>
              <Typography
                color="text.secondary"
                sx={{ mt: 0.75, maxWidth: 430, mx: "auto" }}
              >
                Add a PDF to extract its test requirements. Save it under a
                customer company so you can find it again when a new version
                arrives.
              </Typography>
              <Button
                component={RouterLink}
                to="/specifications"
                sx={{ mt: 2 }}
                endIcon={<ArrowForwardRoundedIcon />}
              >
                Open PDF review
              </Button>
            </Box>
          ) : (
            <TableContainer
              sx={{ maxHeight: "calc(100dvh - 280px)", minHeight: 240 }}
            >
              <Table
                stickyHeader
                size="small"
                aria-label="Saved customer specifications"
                sx={{
                  "& th": {
                    bgcolor: "#f5f7fb",
                    color: "#53647b",
                    fontWeight: 650,
                  },
                  "& td": { py: 1.75, borderColor: "#e6ebf2" },
                }}
              >
                <TableHead>
                  <TableRow>
                    <TableCell>Customer</TableCell>
                    <TableCell>Specification</TableCell>
                    <TableCell>Spec date</TableCell>
                    <TableCell align="right">Tests</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Updated</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {filtered.map((document) => (
                    <TableRow
                      key={document.family_id}
                      selected={document.family_id === selectedFamily}
                      sx={{ "&.Mui-selected": { bgcolor: "#f0f5ff" } }}
                    >
                      <TableCell
                        sx={{
                          color: document.company_name
                            ? "text.primary"
                            : "text.secondary",
                        }}
                      >
                        {document.company_name || "Not assigned"}
                      </TableCell>
                      <TableCell sx={{ minWidth: 220 }}>
                        <Button
                          onClick={() =>
                            setParams({ document: document.document_id })
                          }
                          sx={{
                            px: 0,
                            minWidth: 0,
                            textAlign: "left",
                            justifyContent: "flex-start",
                          }}
                        >
                          {documentTitle(document)}
                        </Button>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          display="block"
                        >
                          {document.specification_id && document.title
                            ? `${document.specification_id} · `
                            : ""}
                          {document.original_name}
                        </Typography>
                        {(versionGroups.get(document.family_id)?.length ?? 0) >
                          1 && (
                          <Button
                            size="small"
                            startIcon={
                              <HistoryRoundedIcon sx={{ fontSize: 15 }} />
                            }
                            onClick={() =>
                              setVersionsFamilyId(document.family_id)
                            }
                            sx={{ mt: 0.25, p: 0, minWidth: 0, fontSize: 12 }}
                          >
                            {versionGroups.get(document.family_id)!.length}{" "}
                            versions
                          </Button>
                        )}
                      </TableCell>
                      <TableCell>
                        {specificationVersionLabel(document)}
                      </TableCell>
                      <TableCell align="right">
                        {document.run_id ? document.requirement_count : "—"}
                      </TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          variant="outlined"
                          color={
                            document.review_count > 0 ? "warning" : "default"
                          }
                          label={
                            canDevelop &&
                            document.provider === "manual_reference"
                              ? "Prepared example"
                              : !document.run_id
                                ? "Ready to analyze"
                                : document.review_count > 0
                                  ? `${document.review_count} to review`
                                  : "Extracted"
                          }
                        />
                      </TableCell>
                      <TableCell
                        sx={{ whiteSpace: "nowrap", color: "text.secondary" }}
                      >
                        {dateLabel(document.updated_at)}
                      </TableCell>
                      <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                        <Tooltip title="Review PDF">
                          <IconButton
                            size="small"
                            component={RouterLink}
                            to={reviewUrl(document.document_id)}
                            aria-label={`Review ${documentTitle(document)}`}
                          >
                            <DescriptionOutlinedIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Remove from library">
                          <IconButton
                            size="small"
                            aria-label={`Remove ${documentTitle(document)}`}
                            onClick={() => {
                              setRemoving(document);
                              setRemoveError("");
                              setRemoveStale(false);
                            }}
                          >
                            <DeleteOutlineRoundedIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))}
                  {filtered.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={7}
                        sx={{
                          py: 4,
                          textAlign: "center",
                          color: "text.secondary",
                        }}
                      >
                        No specifications match this search.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Paper>
      )}

      {detail.loading && !detail.data && selectedId && (
        <Box sx={{ p: 4, textAlign: "center" }}>
          <CircularProgress size={24} />
        </Box>
      )}
      {detail.error && (
        <Alert
          severity="error"
          action={<Button onClick={detail.refresh}>Retry</Button>}
        >
          {detail.error}
        </Alert>
      )}
      {selectedId && current && detail.data && (
        <Paper variant="outlined" sx={{ overflow: "hidden" }}>
          <DocumentRevisionNotice
            issues={current.document_review_issues}
            sourceReviewUrl={reviewUrl(current.document_id)}
          />
          <Box
            sx={{
              p: 2,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            {canDevelop && current.provider === "manual_reference" && (
              <Alert severity="info" sx={{ mb: 1.5, py: 0.25 }}>
                Manually prepared example requirements. Analyze the PDF to
                evaluate a model.
              </Alert>
            )}
            <Stack
              direction={{ xs: "column", md: "row" }}
              spacing={2}
              justifyContent="space-between"
              alignItems={{ md: "flex-start" }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="caption" color="text.secondary">
                  SELECTED SPECIFICATION
                </Typography>
                <Typography variant="h6" sx={{ mt: 0.5 }}>
                  {documentTitle(current)}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  {current.company_name || "Customer not assigned"}
                  {current.specification_id
                    ? ` · ${current.specification_id}`
                    : ""}
                  {` · ${specificationVersionLabel(current)}`}
                </Typography>
              </Box>
              <Stack
                direction="row"
                spacing={1}
                flexWrap="wrap"
                useFlexGap
                sx={{ flexShrink: 0 }}
              >
                <Button
                  component={RouterLink}
                  to={reviewUrl(
                    current.document_id,
                    detailTab === "tests"
                      ? selectedTest?.requirement_id
                      : selectedTestId || undefined,
                  )}
                  variant="contained"
                  endIcon={<ArrowForwardRoundedIcon />}
                >
                  Read PDF & review
                </Button>
                <Button
                  component={RouterLink}
                  to={`/reports?document=${encodeURIComponent(current.document_id)}`}
                  startIcon={<AssessmentOutlinedIcon />}
                  variant="outlined"
                >
                  Gap report
                </Button>
                <Tooltip title="Specification actions">
                  <IconButton
                    id="specification-actions-button"
                    aria-label="Specification actions"
                    aria-haspopup="menu"
                    aria-expanded={Boolean(specificationActions)}
                    aria-controls={
                      specificationActions
                        ? "specification-actions-menu"
                        : undefined
                    }
                    onClick={(event) =>
                      setSpecificationActions(event.currentTarget)
                    }
                  >
                    <MoreHorizRoundedIcon />
                  </IconButton>
                </Tooltip>
                <Menu
                  id="specification-actions-menu"
                  anchorEl={specificationActions}
                  open={Boolean(specificationActions)}
                  onClose={() => setSpecificationActions(null)}
                  slotProps={{
                    list: {
                      "aria-labelledby": "specification-actions-button",
                    },
                  }}
                >
                  <MenuItem
                    onClick={() => {
                      setSpecificationActions(null);
                      openEdit(current);
                    }}
                  >
                    <EditOutlinedIcon fontSize="small" sx={{ mr: 1 }} />
                    Edit details
                  </MenuItem>
                  <MenuItem
                    onClick={() => {
                      setSpecificationActions(null);
                      setRemoving(current);
                      setRemoveError("");
                      setRemoveStale(false);
                    }}
                  >
                    <DeleteOutlineRoundedIcon fontSize="small" sx={{ mr: 1 }} />
                    Remove from library
                  </MenuItem>
                </Menu>
              </Stack>
            </Stack>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1.5}
              alignItems={{ sm: "center" }}
              sx={{ mt: 2 }}
            >
              {detail.data.versions.length > 1 && (
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<HistoryRoundedIcon fontSize="small" />}
                  onClick={() => setVersionsFamilyId(current.family_id)}
                  sx={{ alignSelf: "flex-start", flexShrink: 0 }}
                >
                  Versions ({detail.data.versions.length})
                </Button>
              )}
              <Typography variant="caption" color="text.secondary">
                {current.original_name} · {current.page_count} pages ·{" "}
                {current.requirement_count} tests
              </Typography>
            </Stack>
          </Box>
          {detail.data.requirements.length === 0 ? (
            <Box sx={{ p: 4 }}>
              <Typography fontWeight={600}>
                No extracted requirements yet
              </Typography>
              <Typography color="text.secondary" sx={{ mt: 0.5 }}>
                The PDF is saved. Choose Read PDF &amp; review above to extract
                its test requirements.
              </Typography>
            </Box>
          ) : (
            <>
              <Tabs
                value={detailTab}
                onChange={(_, value) => setDetailTab(value)}
                aria-label="Saved specification data"
                sx={{
                  px: { xs: 1, md: 2 },
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Tab
                  value="tests"
                  label={`Test items (${detail.data.requirements.length})`}
                />
                <Tab value="records" label="Detailed records" />
              </Tabs>
              {detailTab === "tests" && (
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: {
                      xs: "minmax(0, 1fr)",
                      md: "270px minmax(0, 1fr)",
                    },
                  }}
                >
                  <List
                    aria-label="Saved test items"
                    disablePadding
                    sx={{
                      maxHeight: { xs: 240, md: 560 },
                      overflowY: "auto",
                      borderRight: { md: "1px solid #e2e8f0" },
                      borderBottom: { xs: "1px solid #e2e8f0", md: 0 },
                      borderColor: "divider",
                    }}
                  >
                    {detail.data.requirements.map((requirement) => (
                      <ListItemButton
                        key={requirement.requirement_id}
                        aria-label={requirement.test_name}
                        selected={
                          selectedTest?.requirement_id ===
                          requirement.requirement_id
                        }
                        aria-current={
                          selectedTest?.requirement_id ===
                          requirement.requirement_id
                            ? "true"
                            : undefined
                        }
                        onClick={() =>
                          setSelectedTestId(requirement.requirement_id)
                        }
                        sx={{
                          p: 2.5,
                          display: "block",
                          borderBottom: "1px solid",
                          borderColor: "divider",
                          borderLeft: "3px solid transparent",
                          "&.Mui-selected": {
                            bgcolor: "#edf3ff",
                            borderLeftColor: "primary.main",
                          },
                        }}
                      >
                        <Typography variant="body2" fontWeight={600}>
                          {requirement.test_name}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{
                            display: "block",
                            mt: 0.5,
                            overflowWrap: "anywhere",
                          }}
                        >
                          {requirement.methods.join(" · ") ||
                            "Customer-defined procedure"}
                        </Typography>
                      </ListItemButton>
                    ))}
                  </List>
                  {selectedTest && (
                    <Box sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>
                      <Box sx={{ mb: 2.5, minWidth: 0 }}>
                        <Typography
                          variant="h6"
                          sx={{ overflowWrap: "anywhere" }}
                        >
                          {selectedTest.test_name}
                        </Typography>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ mt: 0.5 }}
                        >
                          Saved customer requirements
                        </Typography>
                      </Box>
                      <Stack
                        spacing={2.5}
                        divider={
                          <Box
                            sx={{
                              borderBottom: "1px solid",
                              borderColor: "divider",
                            }}
                          />
                        }
                      >
                        <Box>
                          <Typography
                            variant="body2"
                            fontWeight={600}
                            sx={{ mb: 0.75 }}
                          >
                            Method
                          </Typography>
                          <Typography variant="body2">
                            {selectedTest.methods.join(" · ") ||
                              "Customer-defined procedure"}
                          </Typography>
                        </Box>
                        {(
                          [
                            ["condition", "Conditions and scope"],
                            ["criterion", "Test specification"],
                          ] as const
                        ).map(([kind, label]) => {
                          const claims = selectedTest.claims.filter((claim) =>
                            kind === "criterion"
                              ? ["criterion", "reporting"].includes(
                                  claim.kind || "",
                                )
                              : !["criterion", "reporting"].includes(
                                  claim.kind || "",
                                ),
                          );
                          const facts = claims
                            .filter(
                              (claim) =>
                                !["unknown", "unparsed", "unresolved"].includes(
                                  claim.status,
                                ),
                            )
                            .map((claim) => ({
                              label: claim.label,
                              value: claim.display_value,
                              context: claim.sequence
                                ? `step ${claim.sequence}`
                                : claim.stage &&
                                    !["general", "measurement"].includes(
                                      claim.stage,
                                    )
                                  ? stageLabel(claim.stage)
                                  : undefined,
                            }));
                          return (
                            <Box key={kind}>
                              <Typography
                                variant="body2"
                                fontWeight={600}
                                sx={{ mb: 0.75 }}
                              >
                                {label}
                              </Typography>
                              <FactSummary
                                facts={facts}
                                emptyLabel={
                                  claims.length
                                    ? "Additional wording is saved in the details."
                                    : "Not recorded"
                                }
                                limit={3}
                              />
                            </Box>
                          );
                        })}
                      </Stack>
                      <Box
                        sx={{
                          mt: 3,
                          pt: 2,
                          borderTop: "1px solid",
                          borderColor: "divider",
                        }}
                      >
                        <Button
                          variant="outlined"
                          onClick={() => {
                            setSelectedTestId(selectedTest.requirement_id);
                            setDetailTab("records");
                          }}
                          endIcon={<ArrowForwardRoundedIcon />}
                        >
                          View detailed requirements
                        </Button>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: "block", mt: 1 }}
                        >
                          Includes all saved parameters, stages and links to
                          their source pages.
                        </Typography>
                      </Box>
                    </Box>
                  )}
                </Box>
              )}
              {detailTab === "records" && (
                <>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ px: { xs: 2, md: 3 }, pt: 2, display: "block" }}
                  >
                    {totalRecordCount} saved parameter records across this
                    specification.
                  </Typography>
                  <Box sx={{ px: { xs: 2, md: 3 }, py: 2 }}>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      spacing={1.5}
                    >
                      <FormControl size="small" sx={{ minWidth: 240 }}>
                        <InputLabel id="library-test-label">
                          Test item
                        </InputLabel>
                        <Select
                          labelId="library-test-label"
                          label="Test item"
                          value={selectedTestId}
                          onChange={(event) =>
                            setSelectedTestId(event.target.value)
                          }
                        >
                          <MenuItem value="">All test items</MenuItem>
                          {detail.data.requirements.map((requirement) => (
                            <MenuItem
                              key={requirement.requirement_id}
                              value={requirement.requirement_id}
                            >
                              {requirement.test_name}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                      <TextField
                        size="small"
                        placeholder="Find a parameter or requirement"
                        value={recordQuery}
                        onChange={(event) => setRecordQuery(event.target.value)}
                        inputProps={{
                          "aria-label": "Search requirement records",
                        }}
                        sx={{ flex: 1 }}
                        InputProps={{
                          startAdornment: (
                            <InputAdornment position="start">
                              <SearchRoundedIcon fontSize="small" />
                            </InputAdornment>
                          ),
                        }}
                      />
                    </Stack>
                  </Box>
                  <TableContainer>
                    <Table
                      size="small"
                      aria-label="Saved requirement records"
                      sx={{
                        "& th": { whiteSpace: "nowrap" },
                        "& td": { verticalAlign: "top" },
                      }}
                    >
                      <TableHead>
                        <TableRow>
                          {!selectedTestId && (
                            <TableCell sx={{ pl: { xs: 2, md: 3 } }}>
                              Test item
                            </TableCell>
                          )}
                          <TableCell
                            sx={{
                              pl: selectedTestId ? { xs: 2, md: 3 } : undefined,
                            }}
                          >
                            Parameter
                          </TableCell>
                          <TableCell>Customer requirement</TableCell>
                          <TableCell>Unit</TableCell>
                          <TableCell>Stage / step</TableCell>
                          <TableCell>Source</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {requirementRecords
                          .slice(
                            recordPage * rowsPerPage,
                            (recordPage + 1) * rowsPerPage,
                          )
                          .map(({ key, test, claim }) => (
                            <TableRow key={key} hover>
                              {!selectedTestId && (
                                <TableCell
                                  sx={{ pl: { xs: 2, md: 3 }, minWidth: 160 }}
                                >
                                  {test.test_name}
                                </TableCell>
                              )}
                              <TableCell
                                sx={{
                                  pl: selectedTestId
                                    ? { xs: 2, md: 3 }
                                    : undefined,
                                  minWidth: 150,
                                }}
                              >
                                <Typography variant="body2" fontWeight={550}>
                                  {claim.label}
                                </Typography>
                              </TableCell>
                              <TableCell
                                sx={{
                                  minWidth: 210,
                                  maxWidth: 420,
                                  overflowWrap: "anywhere",
                                }}
                              >
                                <Typography variant="body2">
                                  {requirementValue(
                                    claim.display_value,
                                    claim.unit,
                                  )}
                                </Typography>
                                {["unknown", "unparsed", "unresolved"].includes(
                                  claim.status,
                                ) && (
                                  <Typography
                                    variant="caption"
                                    color="warning.main"
                                  >
                                    Needs interpretation
                                  </Typography>
                                )}
                              </TableCell>
                              <TableCell
                                sx={{
                                  color: "text.secondary",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {claim.unit || "—"}
                              </TableCell>
                              <TableCell
                                sx={{ color: "text.secondary", minWidth: 115 }}
                              >
                                <Typography variant="body2">
                                  {stageLabel(claim.stage)}
                                </Typography>
                                {stepLabel(claim, test.claims) && (
                                  <Typography
                                    variant="caption"
                                    display="block"
                                    color="text.secondary"
                                  >
                                    {stepLabel(claim, test.claims)}
                                  </Typography>
                                )}
                              </TableCell>
                              <TableCell sx={{ whiteSpace: "nowrap" }}>
                                <Button
                                  component={RouterLink}
                                  to={reviewUrl(
                                    current.document_id,
                                    test.requirement_id,
                                    claim.source_page,
                                  )}
                                  size="small"
                                  startIcon={
                                    <OpenInNewRoundedIcon
                                      sx={{ fontSize: "14px !important" }}
                                    />
                                  }
                                >
                                  {claim.source_page
                                    ? `Page ${claim.source_page}`
                                    : "View source"}
                                </Button>
                              </TableCell>
                            </TableRow>
                          ))}
                        {!requirementRecords.length && (
                          <TableRow>
                            <TableCell
                              colSpan={selectedTestId ? 5 : 6}
                              sx={{
                                py: 4,
                                textAlign: "center",
                                color: "text.secondary",
                              }}
                            >
                              No requirement records match this filter.
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </TableContainer>
                  <TablePagination
                    component="div"
                    count={requirementRecords.length}
                    page={recordPage}
                    rowsPerPage={rowsPerPage}
                    rowsPerPageOptions={[10, 20, 50]}
                    labelRowsPerPage="Records per page"
                    onPageChange={(_, page) => setRecordPage(page)}
                    onRowsPerPageChange={(event) =>
                      setRowsPerPage(Number(event.target.value))
                    }
                  />
                </>
              )}
            </>
          )}
        </Paper>
      )}

      <Dialog
        open={Boolean(versionsFamilyId && versionFamily)}
        onClose={() => setVersionsFamilyId(null)}
        maxWidth="sm"
        fullWidth
        scroll="paper"
        aria-labelledby="specification-versions-title"
      >
        <DialogTitle id="specification-versions-heading">
          <Box component="span" id="specification-versions-title">
            Specification versions
          </Box>
          {versionFamily && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mt: 0.5, overflowWrap: "anywhere" }}
            >
              {versionFamily.company_name || "Customer not assigned"} ·{" "}
              {documentTitle(versionFamily)}
            </Typography>
          )}
        </DialogTitle>
        <DialogContent dividers sx={{ p: 0 }}>
          <List disablePadding aria-label="Saved specification versions">
            {versionChoices.map((version) => (
              <ListItemButton
                key={version.document_id}
                selected={version.document_id === selectedId}
                aria-label={`Open version ${specificationVersionLabel(version)}: ${version.original_name}`}
                onClick={() => {
                  setVersionsFamilyId(null);
                  if (version.document_id !== selectedId)
                    setParams({ document: version.document_id });
                }}
                sx={{
                  px: 2.5,
                  py: 1.75,
                  gap: 1.25,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  alignItems: "center",
                }}
              >
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Stack
                    direction="row"
                    alignItems="center"
                    gap={0.75}
                    useFlexGap
                    flexWrap="wrap"
                  >
                    <Typography variant="body2" fontWeight={650}>
                      {specificationVersionLabel(version)}
                    </Typography>
                    {version.document_id === selectedId && (
                      <Chip
                        size="small"
                        label="Viewing"
                        color="primary"
                        variant="outlined"
                        sx={{ height: 20, fontSize: 11 }}
                      />
                    )}
                  </Stack>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mt: 0.5, overflowWrap: "anywhere" }}
                  >
                    {version.original_name}
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mt: 0.25 }}
                  >
                    {version.run_id
                      ? `${version.requirement_count} tests · ${version.review_count ? `${version.review_count} to review` : "Extracted"}`
                      : "Ready to analyze"}
                  </Typography>
                </Box>
                <ChevronRightRoundedIcon fontSize="small" color="action" />
              </ListItemButton>
            ))}
          </List>
        </DialogContent>
        <DialogActions sx={{ px: 2.5, py: 1.5 }}>
          <Button onClick={() => setVersionsFamilyId(null)}>Close</Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={Boolean(removing)}
        onClose={() => {
          if (!removeBusy) setRemoving(null);
        }}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Remove this specification?</DialogTitle>
        <DialogContent>
          <Typography fontWeight={600} sx={{ mb: 1 }}>
            {removing ? documentTitle(removing) : ""}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            This version will no longer appear in your library or report
            selection. Other versions remain available. Its original PDF and
            saved history are retained.
          </Typography>
          {removeError && (
            <Alert
              severity="error"
              sx={{ mt: 2 }}
              action={
                removeStale ? (
                  <Button
                    color="inherit"
                    onClick={() => {
                      setRemoving(null);
                      library.refresh();
                      onRefresh();
                    }}
                  >
                    Reload library
                  </Button>
                ) : undefined
              }
            >
              {removeError}
            </Alert>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}>
          <Button disabled={removeBusy} onClick={() => setRemoving(null)}>
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            disabled={removeBusy || removeStale}
            onClick={() => void removeSpecification()}
          >
            {removeBusy ? "Removing…" : "Remove from library"}
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={dialogOpen}
        onClose={(_, reason) => {
          if (!saving && reason !== "backdropClick") setDialogOpen(false);
        }}
        disableEscapeKeyDown={saving}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {editing ? "Specification details" : "Add customer specification"}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2.25} sx={{ pt: 0.75 }}>
            {!editing && (
              <Box
                component="section"
                aria-label="Specification PDF"
                sx={{
                  p: 2,
                  border: "1px solid",
                  borderColor: file ? "primary.light" : "divider",
                  borderRadius: 2,
                  bgcolor: file ? "#f5f8ff" : "#f8fafc",
                }}
              >
                <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                  Specification PDF
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mb: 1.5 }}
                >
                  Text-based PDF · up to 25 MB
                </Typography>
                {file && (
                  <Stack direction="row" gap={1.25} sx={{ mb: 1.5 }}>
                    <DescriptionOutlinedIcon
                      color="primary"
                      sx={{ mt: 0.25, flexShrink: 0 }}
                    />
                    <Box sx={{ minWidth: 0 }}>
                      <Typography
                        variant="body2"
                        fontWeight={600}
                        sx={{ overflowWrap: "anywhere" }}
                      >
                        {file.name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {file.size < 1024 * 1024
                          ? `${Math.max(1, Math.round(file.size / 1024))} KB`
                          : `${(file.size / (1024 * 1024)).toFixed(1)} MB`}
                        {uploadedId
                          ? " · Uploaded"
                          : " · Selected, not uploaded yet"}
                      </Typography>
                    </Box>
                  </Stack>
                )}
                <Stack direction="row" gap={1} useFlexGap flexWrap="wrap">
                  <Button
                    component="label"
                    variant={file ? "outlined" : "contained"}
                    startIcon={<UploadFileOutlinedIcon />}
                    disabled={saving || Boolean(uploadedId)}
                  >
                    {file ? "Replace PDF" : "Choose PDF"}
                    <input
                      ref={fileInput}
                      type="file"
                      hidden
                      accept="application/pdf,.pdf"
                      aria-label="Choose specification PDF"
                      disabled={saving || Boolean(uploadedId)}
                      onChange={(event) => {
                        const selectedFile = event.target.files?.[0];
                        if (selectedFile) {
                          setFile(selectedFile);
                          setSaveError("");
                        }
                        event.currentTarget.value = "";
                      }}
                    />
                  </Button>
                  {file && (
                    <Button
                      startIcon={<DeleteOutlineRoundedIcon />}
                      disabled={saving || Boolean(uploadedId)}
                      onClick={() => {
                        setFile(null);
                        setSaveError("");
                        if (fileInput.current) fileInput.current.value = "";
                      }}
                    >
                      Remove PDF
                    </Button>
                  )}
                </Stack>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  display="block"
                  sx={{ mt: 1.25 }}
                >
                  Scanned PDFs are not supported yet. OCR is coming soon.
                </Typography>
              </Box>
            )}
            {!editing && (
              <Divider textAlign="left">
                <Typography variant="subtitle2" color="text.secondary">
                  Specification details
                </Typography>
              </Divider>
            )}
            {!editing && (
              <FormControl fullWidth size="small">
                <InputLabel id="spec-family-label">Save under</InputLabel>
                <Select
                  labelId="spec-family-label"
                  label="Save under"
                  value={metadata.family_id ?? ""}
                  disabled={saving}
                  onChange={(event) => chooseFamily(event.target.value)}
                >
                  <MenuItem value="">A new specification</MenuItem>
                  {families.map((family) => (
                    <MenuItem key={family.family_id} value={family.family_id}>
                      {family.company_name || "Not assigned"} ·{" "}
                      {family.specification_id || documentTitle(family)}
                    </MenuItem>
                  ))}
                </Select>
                <FormHelperText>
                  {metadata.family_id
                    ? "A new version of this specification. Earlier versions are kept."
                    : families.length
                      ? "Start a new specification, or select an existing one to add a version."
                      : "Your first upload starts a new specification. You can add versions later."}
                </FormHelperText>
              </FormControl>
            )}
            <TextField
              label="Customer company"
              value={metadata.company_name}
              onChange={(event) =>
                setMetadata({ ...metadata, company_name: event.target.value })
              }
              disabled={saving}
              fullWidth
              helperText="Use the customer's company name. You can add it later if unknown."
            />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                label="Specification number"
                value={metadata.specification_id}
                onChange={(event) =>
                  setMetadata({
                    ...metadata,
                    specification_id: event.target.value,
                  })
                }
                disabled={saving}
                fullWidth
                placeholder="e.g. CSP-EN-301"
              />
              <TextField
                label="Specification date"
                type="date"
                value={metadata.specification_date ?? ""}
                onChange={(event) =>
                  setMetadata({
                    ...metadata,
                    specification_date: event.target.value || null,
                  })
                }
                disabled={saving}
                fullWidth
                slotProps={{ inputLabel: { shrink: true } }}
                helperText="Use the date printed on the specification. Leave blank if unknown."
              />
            </Stack>
            <TextField
              label="Specification title"
              value={metadata.title}
              onChange={(event) =>
                setMetadata({ ...metadata, title: event.target.value })
              }
              disabled={saving}
              fullWidth
            />
            {editing?.revision && (
              <Typography variant="caption" color="text.secondary">
                Original source revision: {editing.revision}
              </Typography>
            )}
            {editing && (
              <Typography variant="caption" color="text.secondary">
                These details organize the saved customer specification. Earlier
                versions remain available.
              </Typography>
            )}
            {saveError && (
              <Alert severity="error">
                {saveError}
                {uploadedId && (
                  <Typography
                    variant="caption"
                    display="block"
                    sx={{ mt: 0.5 }}
                  >
                    Upload complete. Retry to finish saving these details.
                  </Typography>
                )}
              </Alert>
            )}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}>
          <Button disabled={saving} onClick={() => setDialogOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? "Saving…" : editing ? "Save details" : "Save & open PDF"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
