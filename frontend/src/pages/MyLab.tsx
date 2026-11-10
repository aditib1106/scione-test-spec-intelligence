import { useEffect, useMemo, useState } from "react";
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
  CircularProgress,
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
  Typography,
} from "@mui/material";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import ScienceOutlinedIcon from "@mui/icons-material/ScienceOutlined";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import { ApiError, request, useResource } from "../api";
import type {
  AtomicClaim,
  Capability,
  LabRecords,
  LabProfile,
  OperatingLimit,
  PageProps,
} from "../types";
import CapabilityEditor from "./CapabilityEditor";

const labelFor = (value: string) =>
  value.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());

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
const measurements = (capability: Capability) =>
  claimSummary(capability, "reporting") ||
  (capability.notes ?? "")
    .replace(/^Reportable outputs:\s*/i, "")
    .split(/\nSource:/i)[0]
    .trim();
const claimValue = (claim: AtomicClaim) => {
  if (claim.availability === "unknown") return "Not yet assessed";
  if (claim.availability === "unsupported") return "Not available";
  let text = claim.text_value ?? claim.raw_text;
  if (claim.operator === "range")
    text = `${claim.minimum ?? "…"}–${claim.maximum ?? "…"}`;
  else if (claim.operator === "one_of") text = claim.values.join(" / ");
  else if (claim.value !== null)
    text = `${claim.operator === "=" ? "" : `${claim.operator} `}${claim.value}${claim.tolerance !== null ? ` ± ${claim.tolerance}` : ""}`;
  return `${text}${claim.unit ? ` ${claim.unit}` : ""}`;
};
const claimSummary = (capability: Capability, kind: AtomicClaim["kind"]) => {
  const claims = (capability.atomic_claims ?? []).filter((claim) =>
    kind === "condition"
      ? ["condition", "scope", "instruction"].includes(claim.kind)
      : ["reporting", "criterion"].includes(claim.kind),
  );
  return (
    claims
      .slice(0, 2)
      .map((claim) => `${claim.label}: ${claimValue(claim)}`)
      .join("; ") + (claims.length > 2 ? `; +${claims.length - 2} more` : "")
  );
};
const limitText = (limit: OperatingLimit) => {
  if (limit.supported_values.length) return limit.supported_values.join("; ");
  const value = limit.values.length
    ? limit.values.join(" / ")
    : `${limit.minimum ?? "…"}–${limit.maximum ?? "…"}`;
  return `${value}${limit.unit ? ` ${limit.unit}` : ""}`;
};

export default function MyLab({ workspace, onRefresh }: PageProps) {
  const [params, setParams] = useSearchParams();
  const { lab, catalog } = workspace;
  const [query, setQuery] = useState("");
  const [editingCapability, setEditingCapability] = useState<
    Capability | undefined
  >();
  const [capabilityOpen, setCapabilityOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileName, setProfileName] = useState(lab.name);
  const [profileDescription, setProfileDescription] = useState(lab.description);
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [busyExample, setBusyExample] = useState(false);
  const [exampleError, setExampleError] = useState("");
  const [exampleNotice, setExampleNotice] = useState("");
  const [saveError, setSaveError] = useState("");
  const [example, setExample] = useState("csp301");
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

  async function saveCapability(capability: Capability) {
    setSaveError("");
    const capabilities = editingCapability
      ? lab.capabilities.map((item) =>
          item.id === editingCapability.id ? capability : item,
        )
      : [...lab.capabilities, capability];
    const updated = await request<LabProfile>("/api/lab", {
      method: "PUT",
      body: JSON.stringify({ ...lab, capabilities }),
    });
    setCapabilityOpen(false);
    setEditingCapability(undefined);
    if (!updated) return;
    onRefresh();
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

  async function loadExample() {
    setBusyExample(true);
    setExampleError("");
    setExampleNotice("");
    try {
      await request<LabProfile>("/api/lab/example", {
        method: "POST",
        body: JSON.stringify({ version: lab.version, example }),
      });
      setExampleNotice("Example capabilities loaded.");
      onRefresh();
    } catch (reason) {
      const message =
        reason instanceof Error
          ? reason.message
          : "Example capabilities could not be loaded.";
      setExampleError(
        reason instanceof ApiError && reason.status === 409
          ? `${message} Reload the latest lab and try again.`
          : message,
      );
    } finally {
      setBusyExample(false);
    }
  }

  const viewed = lab.capabilities.find((item) => item.id === viewingId);
  const viewedClaims =
    savedParameters.data?.lab_version === lab.version
      ? savedParameters.data.claims.filter(
          (claim) => claim.capability_id === viewingId,
        )
      : [];
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
      {exampleError && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" onClick={onRefresh}>
              Reload lab
            </Button>
          }
        >
          {exampleError}
        </Alert>
      )}
      {exampleNotice && (
        <Alert
          severity="success"
          sx={{ mb: 2 }}
          onClose={() => setExampleNotice("")}
        >
          {exampleNotice}
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
              <TextField
                select
                size="small"
                label="Example"
                value={example}
                onChange={(event) => setExample(event.target.value)}
                sx={{ minWidth: 185 }}
              >
                <MenuItem value="csp301_demo">
                  CSP-EN-301 · updated demonstration lab
                </MenuItem>
                <MenuItem value="csp301">CSP-EN-301 · four tests</MenuItem>
                <MenuItem value="csp301_detailed">
                  CSP-EN-301 · detailed synthetic capabilities
                </MenuItem>
                <MenuItem value="legacy">MS-PNT-410 · pilot lab</MenuItem>
              </TextField>
              <Button
                variant="outlined"
                onClick={loadExample}
                disabled={busyExample}
              >
                {busyExample ? (
                  <CircularProgress size={18} />
                ) : (
                  "Load example lab"
                )}
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
                  <TableCell sx={{ width: "25%" }}>
                    Criteria / reporting
                  </TableCell>
                  <TableCell sx={{ width: "13%" }}>Rate</TableCell>
                  <TableCell sx={{ width: "7%" }} align="right">
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
                          {claimSummary(capability, "condition") ||
                            capability.declared_constraints ||
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
                          {measurements(capability) || "Not specified"}
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
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Card>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 1.5 }}
      >
        Lab declarations and reference rates require review before use in a
        quote. Example capabilities are illustrative.
      </Typography>

      <Dialog
        open={Boolean(viewed)}
        onClose={() => setViewingId(null)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          {viewed
            ? (tests.get(viewed.canonical_test_id)?.name ?? "Lab capability")
            : "Lab capability"}
        </DialogTitle>
        <DialogContent dividers>
          {viewed && (
            <Stack spacing={2.5}>
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
                  Declared methods
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
                        label={method!.identifier}
                      />
                    ))}
                  {!viewed.method_ids.length && (
                    <Typography color="text.secondary">
                      No method recorded.
                    </Typography>
                  )}
                </Stack>
              </Box>
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
                  Conditions and scope
                </Typography>
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                  {claimSummary(viewed, "condition") ||
                    viewed.declared_constraints ||
                    "Not specified"}
                </Typography>
              </Box>
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
                  Criteria / reporting
                </Typography>
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                  {measurements(viewed) || "Not specified"}
                </Typography>
              </Box>
              <Accordion
                expanded={parametersOpen}
                onChange={(_, expanded) => setParametersOpen(expanded)}
                disableGutters
                elevation={0}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  "&:before": { display: "none" },
                }}
              >
                <AccordionSummary
                  id="saved-capability-parameters-heading"
                  aria-controls="saved-capability-parameters-content"
                  expandIcon={<ExpandMoreRoundedIcon />}
                >
                  <Box>
                    <Typography variant="subtitle2">
                      Detailed parameters
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      View saved operating conditions and reportable
                      measurements.
                    </Typography>
                  </Box>
                </AccordionSummary>
                <AccordionDetails>
                  <Stack spacing={2}>
                    {savedParameters.loading && (
                      <Stack direction="row" spacing={1} alignItems="center">
                        <CircularProgress size={18} />
                        <Typography variant="body2">
                          Loading saved parameters…
                        </Typography>
                      </Stack>
                    )}
                    {savedParameters.error && (
                      <Alert
                        severity="error"
                        action={
                          <Button
                            color="inherit"
                            onClick={savedParameters.refresh}
                          >
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
                          parameters.
                        </Alert>
                      )}
                    {viewedClaims.length > 0 && (
                      <TableContainer>
                        <Table
                          size="small"
                          aria-label="Saved capability parameters"
                          sx={{ minWidth: 640 }}
                        >
                          <TableHead>
                            <TableRow>
                              <TableCell>Parameter</TableCell>
                              <TableCell>Declared capability</TableCell>
                              <TableCell>Stage / context</TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {viewedClaims.map((claim) => (
                              <TableRow key={claim.id}>
                                <TableCell sx={{ verticalAlign: "top" }}>
                                  <Typography variant="body2" fontWeight={600}>
                                    {claim.label}
                                  </Typography>
                                  <Typography
                                    variant="caption"
                                    color="text.secondary"
                                  >
                                    {claim.kind === "reporting"
                                      ? "Measurement / reporting"
                                      : labelFor(claim.kind)}
                                  </Typography>
                                </TableCell>
                                <TableCell
                                  sx={{ verticalAlign: "top", maxWidth: 300 }}
                                >
                                  <Typography
                                    variant="body2"
                                    sx={{ overflowWrap: "anywhere" }}
                                  >
                                    {claimValue(claim)}
                                  </Typography>
                                  {claim.availability === "unsupported" ? (
                                    <Chip
                                      size="small"
                                      label="Not available"
                                      color="error"
                                      variant="outlined"
                                    />
                                  ) : claim.availability === "unknown" ||
                                    claim.normalization_state ===
                                      "unresolved" ? (
                                    <Typography
                                      variant="caption"
                                      color="warning.main"
                                    >
                                      Needs clarification
                                    </Typography>
                                  ) : null}
                                </TableCell>
                                <TableCell sx={{ verticalAlign: "top" }}>
                                  <Typography variant="body2">
                                    {labelFor(claim.stage)}
                                  </Typography>
                                  {claim.source_context && (
                                    <Typography
                                      variant="caption"
                                      color="text.secondary"
                                      sx={{ display: "block" }}
                                    >
                                      {claim.source_context}
                                    </Typography>
                                  )}
                                  {claim.configuration && (
                                    <Typography
                                      variant="caption"
                                      sx={{ display: "block" }}
                                    >
                                      Setup: {claim.configuration}
                                    </Typography>
                                  )}
                                  {claim.sequence !== null && (
                                    <Typography
                                      variant="caption"
                                      sx={{ display: "block" }}
                                    >
                                      Step {claim.sequence}
                                    </Typography>
                                  )}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </TableContainer>
                    )}
                    {!savedParameters.loading &&
                      !savedParameters.error &&
                      savedParameters.data?.lab_version === lab.version &&
                      viewedClaims.length === 0 && (
                        <Typography variant="body2" color="text.secondary">
                          No detailed parameters have been recorded for this
                          test. Edit the capability to add them.
                        </Typography>
                      )}
                    {viewed.structured_limits.length > 0 && (
                      <Box>
                        <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
                          General operating limits
                        </Typography>
                        <TableContainer>
                          <Table
                            size="small"
                            aria-label="Declared operating limits"
                          >
                            <TableHead>
                              <TableRow>
                                <TableCell>Parameter</TableCell>
                                <TableCell>Declared support</TableCell>
                              </TableRow>
                            </TableHead>
                            <TableBody>
                              {viewed.structured_limits.map((limit, index) => (
                                <TableRow key={`${limit.parameter}-${index}`}>
                                  <TableCell
                                    sx={{ textTransform: "capitalize" }}
                                  >
                                    {limit.parameter.replaceAll("_", " ")}
                                  </TableCell>
                                  <TableCell>{limitText(limit)}</TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </TableContainer>
                      </Box>
                    )}
                  </Stack>
                </AccordionDetails>
              </Accordion>
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
                  Reference rate
                </Typography>
                <Typography variant="body2">
                  {money(viewed) === "—" ? "Not priced" : money(viewed)}
                </Typography>
              </Box>
              {viewed.notes?.match(/\nSource:\s*(.*)/i)?.[1] && (
                <Typography variant="caption" color="text.secondary">
                  Source: {viewed.notes.match(/\nSource:\s*(.*)/i)?.[1]}
                </Typography>
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
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
