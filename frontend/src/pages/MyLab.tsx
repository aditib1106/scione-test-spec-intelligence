import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  InputAdornment,
  MenuItem,
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
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import ScienceOutlinedIcon from "@mui/icons-material/ScienceOutlined";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import DeleteOutlineRoundedIcon from "@mui/icons-material/DeleteOutlineRounded";
import { ApiError, request, useResource } from "../api";
import type {
  AtomicClaim,
  Capability,
  CapabilitySubmission,
  CapabilitySaveResponse,
  LabRecords,
  LabProfile,
  PageProps,
} from "../types";
import CapabilityEditor from "./CapabilityEditor";
import LabCapabilityDetails, {
  capabilityDetailSx,
  labClaimValue as claimValue,
} from "../components/LabCapabilityDetails";
import {
  serviceClaims,
  sharedDeclarationKey,
  sharedDeclarations,
} from "../components/labCapabilityPresentation";

const money = (capability: Capability) =>
  capability.price
    ? `${capability.currency ?? ""} ${capability.price}${capability.pricing_basis ? ` / ${capability.pricing_basis}` : ""}`.trim()
    : "—";

const previewSx = {
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
} as const;
const measurements = (capability: Capability, sharedKeys: Set<string>) =>
  claimSummary(capability, "reporting", sharedKeys) ||
  ((capability.atomic_claims ?? []).length
    ? ""
    : (capability.notes ?? "")
        .replace(/^Reportable outputs:\s*/i, "")
        .split(/\nSource:/i)[0]
        .trim());
const claimSummary = (
  capability: Capability,
  kind: AtomicClaim["kind"],
  sharedKeys: Set<string>,
) => {
  const claims = serviceClaims(
    capability.atomic_claims ?? [],
    sharedKeys,
  ).filter((claim) =>
    kind === "condition"
      ? ["condition", "scope", "instruction"].includes(claim.kind)
      : ["reporting", "criterion"].includes(claim.kind),
  );
  const quantitative = claims.find(
    (claim) => !["text", "required"].includes(claim.operator),
  );
  const preview = quantitative
    ? [quantitative, ...claims.filter((claim) => claim !== quantitative)].slice(
        0,
        2,
      )
    : claims.slice(0, 2);
  return (
    preview.map((claim) => `${claim.label}: ${claimValue(claim)}`).join("; ") +
    (claims.length > 2 ? `; +${claims.length - 2} more` : "")
  );
};
function SharedLabDeclarations({
  claims,
  count,
  editControls,
}: {
  claims: AtomicClaim[];
  count: number;
  editControls?: ReactNode;
}) {
  if (!claims.length) return null;
  return (
    <Accordion disableGutters elevation={0} sx={capabilityDetailSx}>
      <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
        <Box>
          <Typography variant="subtitle1" fontWeight={650}>
            Common entries ({claims.length})
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3 }}>
            Identical entries recorded on all {count} tests.
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails
        sx={{ borderTop: "1px solid", borderColor: "divider", p: 2.25 }}
      >
        <Stack spacing={2} divider={<Divider />}>
          {claims.map((claim) => (
            <Box key={claim.id}>
              <Typography variant="body2" fontWeight={600}>
                {claim.label}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {claimValue(claim)}
              </Typography>
            </Box>
          ))}
          <Box>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mb: editControls ? 1.25 : 0 }}
            >
              {editControls
                ? "Choose a test to edit these entries. Changes apply to that test only."
                : "Use Edit capability to update these entries for this test only."}
            </Typography>
            {editControls}
          </Box>
        </Stack>
      </AccordionDetails>
    </Accordion>
  );
}

export default function MyLab({ workspace, onRefresh }: PageProps) {
  const [params, setParams] = useSearchParams();
  const { lab, catalog } = workspace;
  const sharedClaims = useMemo(
    () => sharedDeclarations(lab.capabilities),
    [lab.capabilities],
  );
  const sharedKeys = useMemo(
    () =>
      new Set(
        sharedClaims
          .map(sharedDeclarationKey)
          .filter((key): key is string => key !== null),
      ),
    [sharedClaims],
  );
  const [query, setQuery] = useState("");
  const [commonEditingId, setCommonEditingId] = useState("");
  const [editingCapability, setEditingCapability] = useState<
    Capability | undefined
  >();
  const [capabilityOpen, setCapabilityOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileName, setProfileName] = useState(lab.name);
  const [profileDescription, setProfileDescription] = useState(lab.description);
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [deletingCapability, setDeletingCapability] =
    useState<Capability | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [viewingId, setViewingId] = useState<string | null>(null);
  const [parametersOpen, setParametersOpen] = useState(false);
  useEffect(() => setParametersOpen(false), [viewingId]);
  const savedParameters = useResource<LabRecords>(
    parametersOpen && viewingId
      ? `/api/lab/records?version=${lab.version}`
      : null,
  );

  useEffect(() => {
    const id = params.get("capability");
    const capability = lab.capabilities.find((item) => item.id === id);
    if (capability) {
      setEditingCapability(capability);
      setCapabilityOpen(true);
      const next = new URLSearchParams(params);
      next.delete("capability");
      setParams(next, { replace: true });
    }
  }, [params, lab.capabilities, setParams]);

  const tests = useMemo(
    () => new Map(catalog.tests.map((test) => [test.id, test])),
    [catalog.tests],
  );
  const methods = useMemo(
    () => new Map(catalog.methods.map((method) => [method.id, method])),
    [catalog.methods],
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return lab.capabilities.filter((capability) => {
      if (!needle) return true;
      const test = tests.get(capability.canonical_test_id);
      const methodText = capability.method_ids
        .map((id) => methods.get(id))
        .filter(Boolean)
        .map(
          (method) =>
            `${method!.identifier} ${method!.name} ${method!.edition ?? ""} ${method!.aliases.map((a) => a.text).join(" ")}`,
        )
        .join(" ");
      return `${test?.name ?? capability.canonical_test_id} ${test?.id ?? ""} ${test?.aliases.map((alias) => alias.text).join(" ") ?? ""} ${methodText} ${capability.declared_constraints ?? ""} ${(capability.atomic_claims ?? []).map((claim) => `${claim.label} ${claim.text_value ?? ""} ${claim.source_context ?? ""}`).join(" ")}`
        .toLocaleLowerCase()
        .includes(needle);
    });
  }, [lab.capabilities, methods, query, tests]);

  async function saveCapability(submission: CapabilitySubmission) {
    setSaveError("");
    const updated = await request<CapabilitySaveResponse>(
      editingCapability
        ? `/api/lab/capabilities/${encodeURIComponent(editingCapability.id)}`
        : "/api/lab/capabilities",
      {
        method: editingCapability ? "PUT" : "POST",
        body: JSON.stringify({ version: lab.version, ...submission }),
      },
    );
    setCapabilityOpen(false);
    setEditingCapability(undefined);
    if (!updated) return;
    onRefresh();
  }

  async function deleteCapability() {
    if (!deletingCapability || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await request<CapabilitySaveResponse>(
        `/api/lab/capabilities/${encodeURIComponent(deletingCapability.id)}`,
        {
          method: "DELETE",
          body: JSON.stringify({ version: lab.version }),
        },
      );
      if (viewingId === deletingCapability.id) setViewingId(null);
      setDeletingCapability(null);
      onRefresh();
    } catch (reason) {
      setDeleteError(
        reason instanceof Error
          ? reason.message
          : "The capability could not be deleted.",
      );
    } finally {
      setDeleting(false);
    }
  }

  async function saveProfile() {
    setSavingProfile(true);
    setProfileError("");
    try {
      await request<LabProfile>("/api/lab", {
        method: "PUT",
        body: JSON.stringify({
          ...lab,
          name: profileName.trim(),
          description: profileDescription,
        }),
      });
      setProfileOpen(false);
      onRefresh();
    } catch (reason) {
      const message =
        reason instanceof Error
          ? reason.message
          : "The lab details could not be saved.";
      setProfileError(
        reason instanceof ApiError && reason.status === 409
          ? `${message} Your draft is still here. Reload the latest lab before trying again.`
          : message,
      );
    } finally {
      setSavingProfile(false);
    }
  }

  const viewed = lab.capabilities.find((item) => item.id === viewingId);
  const viewedClaims =
    savedParameters.data?.lab_version === lab.version
      ? serviceClaims(
          savedParameters.data.claims.filter(
            (claim) => claim.capability_id === viewingId,
          ),
          sharedKeys,
        )
      : serviceClaims(viewed?.atomic_claims ?? [], sharedKeys);
  const editCapability = (capability?: Capability) => {
    setViewingId(null);
    setEditingCapability(capability);
    setCapabilityOpen(true);
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
        alignItems={{ xs: "stretch", sm: "center" }}
        spacing={2}
        sx={{ mb: 3 }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="overline" color="primary.main">
            LAB PROFILE
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="h4" fontWeight={700}>
              {lab.name}
            </Typography>
            <IconButton
              size="small"
              aria-label="Edit lab details"
              onClick={() => {
                setProfileName(lab.name);
                setProfileDescription(lab.description);
                setProfileError("");
                setProfileOpen(true);
              }}
            >
              <EditOutlinedIcon fontSize="small" />
            </IconButton>
          </Stack>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            {lab.description || "Your lab capabilities, methods and pricing."}
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<AddRoundedIcon />}
          onClick={() => editCapability()}
          sx={{ flexShrink: 0, minWidth: 124, whiteSpace: "nowrap" }}
        >
          Add a test
        </Button>
      </Stack>

      <Stack direction="row" spacing={{ xs: 1, sm: 1.5 }} sx={{ mb: 2.5 }}>
        {[
          ["Capabilities", lab.capabilities.length],
          [
            "Methods declared",
            new Set(lab.capabilities.flatMap((cap) => cap.method_ids)).size,
          ],
          [
            "Priced tests",
            lab.capabilities.filter((cap) => cap.price !== null).length,
          ],
        ].map(([label, value]) => (
          <Card
            key={label}
            variant="outlined"
            sx={{ flex: 1, borderColor: "divider" }}
          >
            <CardContent sx={{ py: "14px !important" }}>
              <Typography variant="caption" color="text.secondary">
                {label}
              </Typography>
              <Typography variant="h5" fontWeight={700}>
                {value}
              </Typography>
            </CardContent>
          </Card>
        ))}
      </Stack>

      {saveError && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" onClick={onRefresh}>
              Reload lab
            </Button>
          }
        >
          {saveError}
        </Alert>
      )}
      <Card
        variant="outlined"
        sx={{ borderColor: "divider", overflow: "hidden" }}
      >
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ xs: "stretch", sm: "center" }}
          spacing={1.5}
          sx={{ px: 2, py: 1.5 }}
        >
          <Box>
            <Typography variant="h6" fontWeight={650}>
              Capabilities
            </Typography>
            <Typography variant="caption" color="text.secondary">
              View declared conditions, reportable measurements and rates.
              Select a test for full details.
            </Typography>
          </Box>
          <TextField
            size="small"
            placeholder="Search tests and methods"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            sx={{ width: { xs: "100%", sm: 280 } }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchRoundedIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
        </Stack>
        <Divider />
        {lab.capabilities.length === 0 ? (
          <Stack
            alignItems="center"
            textAlign="center"
            spacing={1.25}
            sx={{ px: 3, py: 6 }}
          >
            <ScienceOutlinedIcon color="disabled" sx={{ fontSize: 36 }} />
            <Typography variant="h6">
              Start with your lab’s capabilities
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ maxWidth: 480 }}
            >
              Add tests and declare the methods, operating conditions and
              pricing your lab supports.
            </Typography>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1}
              sx={{ mt: 1 }}
            >
              <Button
                variant="contained"
                startIcon={<AddRoundedIcon />}
                onClick={() => editCapability()}
              >
                Add a test
              </Button>
            </Stack>
          </Stack>
        ) : filtered.length === 0 ? (
          <Box sx={{ p: 4, textAlign: "center" }}>
            <Typography fontWeight={600}>No matches found</Typography>
            <Typography variant="body2" color="text.secondary">
              Try another test name or method code.
            </Typography>
          </Box>
        ) : (
          <TableContainer>
            <Table
              size="small"
              aria-label="Lab capabilities"
              sx={{ minWidth: 980, tableLayout: "fixed" }}
            >
              <TableHead>
                <TableRow>
                  <TableCell sx={{ width: "17%" }}>Test</TableCell>
                  <TableCell sx={{ width: "15%" }}>Method</TableCell>
                  <TableCell sx={{ width: "23%" }}>Conditions</TableCell>
                  <TableCell sx={{ width: "22%" }}>
                    Acceptance criteria / reporting
                  </TableCell>
                  <TableCell sx={{ width: "13%" }}>Rate</TableCell>
                  <TableCell sx={{ width: "10%" }} align="right">
                    Action
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filtered.map((capability) => {
                  const test = tests.get(capability.canonical_test_id);
                  const declaredMethods = capability.method_ids
                    .map((id) => methods.get(id))
                    .filter(Boolean);
                  return (
                    <TableRow
                      key={capability.id}
                      hover
                      onClick={() => setViewingId(capability.id)}
                      sx={{
                        cursor: "pointer",
                        "& td": { px: 1.5, py: 1.75, verticalAlign: "top" },
                      }}
                    >
                      <TableCell>
                        <Button
                          onClick={(event) => {
                            event.stopPropagation();
                            setViewingId(capability.id);
                          }}
                          sx={{
                            p: 0,
                            minWidth: 0,
                            textAlign: "left",
                            justifyContent: "flex-start",
                          }}
                        >
                          {test?.name ?? "Unrecognized test"}
                        </Button>
                        {test?.id.startsWith("labtest_") && (
                          <Typography
                            variant="caption"
                            sx={{ display: "block", mt: 0.5, color: "#6b46a5" }}
                          >
                            Private test
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" sx={previewSx}>
                          {declaredMethods
                            .map((method) => method!.identifier)
                            .join(" · ") || "Not specified"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={previewSx}
                        >
                          {claimSummary(capability, "condition", sharedKeys) ||
                            (!(capability.atomic_claims ?? []).length &&
                              capability.declared_constraints) ||
                            (capability.structured_limits.length
                              ? `${capability.structured_limits.length} declared operating limits`
                              : "Not specified")}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={previewSx}
                        >
                          {measurements(capability, sharedKeys) ||
                            "Not specified"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" fontWeight={600}>
                          {money(capability) === "—"
                            ? "Not priced"
                            : money(capability)}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Stack
                          direction="row"
                          justifyContent="flex-end"
                          alignItems="center"
                          gap={0.25}
                        >
                          <Button
                            size="small"
                            onClick={(event) => {
                              event.stopPropagation();
                              editCapability(capability);
                            }}
                            sx={{ minWidth: 0, px: 0.5 }}
                          >
                            Edit
                          </Button>
                          <Tooltip title="Delete capability">
                            <IconButton
                              size="small"
                              aria-label={`Delete capability for ${test?.name ?? "this test"}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                setDeleteError("");
                                setDeletingCapability(capability);
                              }}
                              sx={{
                                color: "text.secondary",
                                "&:hover": {
                                  color: "error.main",
                                  bgcolor: "action.hover",
                                },
                              }}
                            >
                              <DeleteOutlineRoundedIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </Stack>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Card>
      {sharedClaims.length > 0 && (
        <Box sx={{ mt: 2 }}>
          <SharedLabDeclarations
            claims={sharedClaims}
            count={lab.capabilities.length}
            editControls={
              <Stack
                direction={{ xs: "column", sm: "row" }}
                gap={1}
                alignItems={{ sm: "center" }}
              >
                <TextField
                  select
                  fullWidth
                  label="Test to edit"
                  value={commonEditingId}
                  onChange={(event) => setCommonEditingId(event.target.value)}
                  sx={{ flex: 1, minWidth: 0 }}
                >
                  <MenuItem value="" disabled>
                    Select a test
                  </MenuItem>
                  {lab.capabilities.map((capability) => (
                    <MenuItem key={capability.id} value={capability.id}>
                      {tests.get(capability.canonical_test_id)?.name ?? "Test"}
                      {capability.method_ids.length > 0 &&
                        ` · ${capability.method_ids
                          .map((id) => methods.get(id)?.identifier)
                          .filter(Boolean)
                          .join(" / ")}`}
                    </MenuItem>
                  ))}
                </TextField>
                <Button
                  variant="outlined"
                  startIcon={<EditOutlinedIcon />}
                  disabled={
                    !lab.capabilities.some(
                      (capability) => capability.id === commonEditingId,
                    )
                  }
                  onClick={() => {
                    const capability = lab.capabilities.find(
                      (item) => item.id === commonEditingId,
                    );
                    if (capability) editCapability(capability);
                  }}
                  sx={{ flexShrink: 0 }}
                >
                  Edit selected test
                </Button>
              </Stack>
            }
          />
        </Box>
      )}
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 1.5 }}
      >
        Lab declarations and reference rates require review before use in a
        quote.
      </Typography>

      <Dialog
        open={Boolean(viewed)}
        onClose={() => setViewingId(null)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ pb: 2 }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mb: 0.5 }}
          >
            Capability · Private to this lab
          </Typography>
          {viewed
            ? (tests.get(viewed.canonical_test_id)?.name ?? "Lab capability")
            : "Lab capability"}
        </DialogTitle>
        <DialogContent dividers sx={{ bgcolor: "grey.50", py: 2.5 }}>
          {viewed && (
            <Stack spacing={1.75}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={2}
                sx={{
                  p: 2.25,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 2,
                  bgcolor: "background.paper",
                }}
              >
                <Box sx={{ flex: 1 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mb: 1 }}
                  >
                    Supported methods
                  </Typography>
                  <Stack direction="row" flexWrap="wrap" gap={0.75}>
                    {viewed.method_ids
                      .map((id) => methods.get(id))
                      .filter(Boolean)
                      .map((method) => (
                        <Chip
                          key={method!.id}
                          size="small"
                          variant="outlined"
                          label={
                            method!.id.startsWith("labmethod_")
                              ? `${method!.identifier} · Private`
                              : method!.identifier
                          }
                        />
                      ))}
                    {!viewed.method_ids.length && (
                      <Typography variant="body2" color="text.secondary">
                        No method recorded.
                      </Typography>
                    )}
                  </Stack>
                </Box>
                <Box sx={{ minWidth: 150 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mb: 1 }}
                  >
                    Reference price
                  </Typography>
                  <Typography variant="body2" fontWeight={600}>
                    {money(viewed) === "—" ? "Not priced" : money(viewed)}
                  </Typography>
                </Box>
              </Stack>
              {savedParameters.error && (
                <Alert
                  severity="error"
                  action={
                    <Button color="inherit" onClick={savedParameters.refresh}>
                      Retry
                    </Button>
                  }
                >
                  {savedParameters.error}
                </Alert>
              )}
              {savedParameters.data &&
                savedParameters.data.lab_version !== lab.version && (
                  <Alert
                    severity="info"
                    action={
                      <Button color="inherit" onClick={onRefresh}>
                        Reload lab
                      </Button>
                    }
                  >
                    Your lab information changed. Reload to see its latest
                    details.
                  </Alert>
                )}
              <LabCapabilityDetails
                key={`${viewed.id}-conditions`}
                title="Operating conditions"
                description="Supported settings, materials and test preparation."
                claims={viewedClaims.filter(
                  (claim) => !["reporting", "criterion"].includes(claim.kind),
                )}
                wording={viewed.declared_constraints}
                limits={viewed.structured_limits}
                onExpand={() => setParametersOpen(true)}
              />
              <LabCapabilityDetails
                key={`${viewed.id}-reporting`}
                title="Measurements & reporting"
                description="What your lab can measure and include in its results."
                claims={viewedClaims.filter((claim) =>
                  ["reporting", "criterion"].includes(claim.kind),
                )}
                wording={viewed.notes
                  ?.replace(/^Reportable outputs:\s*/i, "")
                  .split(/\nSource:/i)[0]
                  .trim()}
                onExpand={() => setParametersOpen(true)}
              />
              <SharedLabDeclarations
                claims={sharedClaims}
                count={lab.capabilities.length}
              />
              {viewed.notes?.match(/\nSource:\s*(.*)/i)?.[1] && (
                <Typography variant="caption" color="text.secondary">
                  Source: {viewed.notes.match(/\nSource:\s*(.*)/i)?.[1]}
                </Typography>
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions sx={{ p: 2, gap: 1 }}>
          <Button onClick={() => setViewingId(null)}>Close</Button>
          <Button
            variant="contained"
            onClick={() => viewed && editCapability(viewed)}
          >
            Edit capability
          </Button>
        </DialogActions>
      </Dialog>

      <CapabilityEditor
        onReload={() => {
          setCapabilityOpen(false);
          setEditingCapability(undefined);
          setSaveError("");
          onRefresh();
        }}
        open={capabilityOpen}
        catalog={catalog}
        initial={editingCapability}
        onClose={() => {
          setCapabilityOpen(false);
          setEditingCapability(undefined);
        }}
        onSave={async (cap) => {
          try {
            await saveCapability(cap);
          } catch (reason) {
            const message =
              reason instanceof Error ? reason.message : "Save failed.";
            setSaveError(
              reason instanceof ApiError && reason.status === 409
                ? `${message} Your changes are still in the editor.`
                : message,
            );
            throw reason;
          }
        }}
      />
      <Dialog
        open={Boolean(deletingCapability)}
        onClose={deleting ? undefined : () => setDeletingCapability(null)}
        fullWidth
        maxWidth="sm"
        aria-labelledby="delete-capability-title"
      >
        <DialogTitle id="delete-capability-title">
          Delete this capability?
        </DialogTitle>
        <DialogContent>
          <Stack spacing={1.5}>
            {deleteError && (
              <Alert
                severity="error"
                action={
                  <Button
                    color="inherit"
                    size="small"
                    onClick={() => {
                      setDeletingCapability(null);
                      onRefresh();
                    }}
                  >
                    Reload lab
                  </Button>
                }
              >
                {deleteError}
              </Alert>
            )}
            <Typography fontWeight={600}>
              {deletingCapability
                ? tests.get(deletingCapability.canonical_test_id)?.name
                : ""}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              This removes the test from your current lab capability. Previously
              saved reports will be kept.
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button
            onClick={() => setDeletingCapability(null)}
            disabled={deleting}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            onClick={deleteCapability}
            disabled={deleting}
          >
            {deleting ? "Deleting…" : "Delete capability"}
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={profileOpen}
        onClose={savingProfile ? undefined : () => setProfileOpen(false)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Edit lab details</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {profileError && (
              <Alert
                severity="error"
                action={
                  profileError.includes("draft is still here") ? (
                    <Button color="inherit" size="small" onClick={onRefresh}>
                      Reload
                    </Button>
                  ) : undefined
                }
              >
                {profileError}
              </Alert>
            )}
            <TextField
              label="Lab name"
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              required
            />
            <TextField
              label="Description"
              value={profileDescription}
              onChange={(e) => setProfileDescription(e.target.value)}
              multiline
              minRows={3}
            />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button
            onClick={() => setProfileOpen(false)}
            disabled={savingProfile}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={saveProfile}
            disabled={savingProfile || !profileName.trim()}
          >
            {savingProfile ? "Saving…" : "Save details"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
