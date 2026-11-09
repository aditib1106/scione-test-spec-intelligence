import { useEffect, useMemo, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import DeleteOutlineRoundedIcon from "@mui/icons-material/DeleteOutlineRounded";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import type {
  AtomicClaim,
  Capability,
  Catalog,
  OperatingLimit,
} from "../types";
import { ApiError } from "../api";
import CapabilityParameters from "../components/CapabilityParameters";
import {
  claimDraft,
  savedClaim,
  type DraftClaim,
} from "../components/labClaimDrafts";

const parameters = [
  "temperature",
  "load",
  "spacing",
  "duration",
  "angle",
  "pressure",
  "cycles",
  "condition",
  "scope",
  "instruction",
] as const;
type Parameter = (typeof parameters)[number];
const units: Partial<Record<Parameter, string>> = {
  temperature: "°C",
  load: "g",
  spacing: "mm",
  duration: "h",
  angle: "°",
  pressure: "bar",
  cycles: "cycles",
};
const labelFor = (v: string) =>
  v.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
const freshLimit = (parameter: Parameter): OperatingLimit => ({
  parameter,
  minimum: null,
  maximum: null,
  values: [],
  unit: units[parameter] ?? null,
  supported_values: [],
});

type DraftLimit = {
  parameter: Parameter;
  minimum: string;
  maximum: string;
  values: string;
  unit: string;
  supported_values: string;
};
function asDraft(limit: OperatingLimit): DraftLimit {
  return {
    parameter: limit.parameter as Parameter,
    minimum: limit.minimum ?? "",
    maximum: limit.maximum ?? "",
    values: limit.values.join("\n"),
    unit: limit.unit ?? "",
    supported_values: limit.supported_values.join("\n"),
  };
}
function fromDraft(limit: DraftLimit): OperatingLimit {
  const numeric = Boolean(units[limit.parameter]);
  return {
    parameter: limit.parameter,
    minimum: numeric && limit.minimum.trim() ? limit.minimum.trim() : null,
    maximum: numeric && limit.maximum.trim() ? limit.maximum.trim() : null,
    values: numeric
      ? limit.values
          .split(/[\n,]+/)
          .map((v) => v.trim())
          .filter(Boolean)
      : [],
    unit: numeric
      ? (units[limit.parameter] ?? (limit.unit.trim() || null))
      : null,
    supported_values: numeric
      ? []
      : limit.supported_values
          .split("\n")
          .map((v) => v.trim())
          .filter(Boolean),
  };
}

interface Props {
  open: boolean;
  catalog: Catalog;
  initial?: Capability;
  onClose: () => void;
  onReload: () => void;
  onSave: (capability: Capability) => Promise<void>;
}
export default function CapabilityEditor({
  open,
  catalog,
  initial,
  onClose,
  onReload,
  onSave,
}: Props) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down("sm"));
  const [testId, setTestId] = useState("");
  const [methodIds, setMethodIds] = useState<string[]>([]);
  const [constraints, setConstraints] = useState("");
  const [notes, setNotes] = useState("");
  const [price, setPrice] = useState("");
  const [currency, setCurrency] = useState("");
  const [basis, setBasis] = useState("");
  const [limitsEnabled, setLimitsEnabled] = useState(false);
  const [limits, setLimits] = useState<DraftLimit[]>([]);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [saving, setSaving] = useState(false);
  const [limitType, setLimitType] = useState<Parameter>("temperature");
  const [claims, setClaims] = useState<DraftClaim[]>([]);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsReviewed, setDetailsReviewed] = useState(false);

  useEffect(() => {
    if (!open) return;
    const firstTest = catalog.tests[0]?.id ?? "";
    const selectedTest = initial?.canonical_test_id ?? firstTest;
    setTestId(selectedTest);
    setMethodIds(initial?.method_ids.slice() ?? []);
    setConstraints(initial?.declared_constraints ?? "");
    setNotes(initial?.notes ?? "");
    setPrice(initial?.price ?? "");
    setCurrency(initial?.currency ?? "");
    setBasis(initial?.pricing_basis ?? "");
    setLimitsEnabled(Boolean(initial?.structured_limits.length));
    setLimits(initial?.structured_limits.map(asDraft) ?? []);
    setClaims(initial?.atomic_claims?.map(claimDraft) ?? []);
    setDetailsOpen(false);
    setDetailsReviewed(false);
    setError("");
    setConflict(false);
    setSaving(false);
    // Refreshing workspace props must not erase an open draft; initialize by record ID.
  }, [open, initial?.id]);

  useEffect(() => {
    setDetailsReviewed(false);
  }, [testId, methodIds, constraints, notes, limitsEnabled, limits]);

  const test = catalog.tests.find((item) => item.id === testId);
  const allowedMethods = useMemo(
    () => catalog.methods.filter((item) => test?.method_ids.includes(item.id)),
    [catalog.methods, test],
  );
  const toggleMethod = (id: string) =>
    setMethodIds((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : [...current, id],
    );
  const patchLimit = (index: number, patch: Partial<DraftLimit>) =>
    setLimits((current) =>
      current.map((limit, i) => (i === index ? { ...limit, ...patch } : limit)),
    );
  const declarationChanged =
    Boolean(initial?.atomic_claims?.length) &&
    (testId !== initial?.canonical_test_id ||
      JSON.stringify([...methodIds].sort()) !==
        JSON.stringify([...(initial?.method_ids ?? [])].sort()) ||
      constraints.trim() !== (initial?.declared_constraints ?? "") ||
      notes.trim() !== (initial?.notes ?? "") ||
      JSON.stringify(limitsEnabled ? limits.map(fromDraft) : []) !==
        JSON.stringify(initial?.structured_limits ?? []));

  async function save() {
    setError("");
    if (declarationChanged && !detailsReviewed) {
      setDetailsOpen(true);
      setError(
        "Review the detailed parameters after changing the test, methods or declaration, then confirm below.",
      );
      return;
    }
    let atomicClaims: AtomicClaim[];
    try {
      atomicClaims = claims.map(savedClaim);
    } catch (reason) {
      setDetailsOpen(true);
      setError(
        reason instanceof Error
          ? reason.message
          : "Check the detailed parameters.",
      );
      return;
    }
    setSaving(true);
    const id = initial?.id ?? `cap_${crypto.randomUUID().replaceAll("-", "")}`;
    const next: Capability = {
      id,
      canonical_test_id: testId,
      method_ids: methodIds.filter((id) =>
        allowedMethods.some((method) => method.id === id),
      ),
      declared_constraints: constraints.trim() || null,
      structured_limits: limitsEnabled ? limits.map(fromDraft) : [],
      atomic_claims: atomicClaims,
      notes: notes.trim() || null,
      price: price.trim() || null,
      currency: price.trim() ? currency.trim().toUpperCase() : null,
      pricing_basis: price.trim() ? basis.trim() : null,
    };
    if (!testId) {
      setError("Choose a test identity.");
      setSaving(false);
      return;
    }
    if (Boolean(price.trim()) !== Boolean(currency.trim() && basis.trim())) {
      setError(
        "Add a currency and pricing basis with the price, or leave all three blank.",
      );
      setSaving(false);
      return;
    }
    try {
      await onSave(next);
    } catch (reason) {
      setConflict(reason instanceof ApiError && reason.status === 409);
      setError(
        reason instanceof Error
          ? reason.message
          : "The capability could not be saved.",
      );
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={saving ? undefined : onClose}
      fullScreen={fullScreen}
      fullWidth
      maxWidth="md"
      scroll="paper"
    >
      <DialogTitle sx={{ pb: 1 }}>
        {initial ? "Edit capability" : "Add a test"}
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          Declare the methods and operating conditions your lab can provide.
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.4}>
          {error && (
            <Alert
              severity="error"
              action={
                conflict ? (
                  <Button color="inherit" size="small" onClick={onReload}>
                    Discard draft & reload
                  </Button>
                ) : undefined
              }
            >
              {error}
            </Alert>
          )}
          <Box>
            <Typography variant="overline" color="text.secondary">
              Test item
            </Typography>
            <TextField
              select
              fullWidth
              label="Test name"
              value={testId}
              onChange={(event) => {
                setTestId(event.target.value);
                setMethodIds([]);
              }}
              sx={{ mt: 1 }}
            >
              {catalog.tests.map((item) => (
                <MenuItem key={item.id} value={item.id}>
                  {item.name}
                </MenuItem>
              ))}
            </TextField>
          </Box>
          <Box>
            <Typography variant="overline" color="text.secondary">
              Methods
            </Typography>
            {allowedMethods.length ? (
              <Stack
                direction="row"
                flexWrap="wrap"
                gap={0.5}
                sx={{ mt: 0.75 }}
              >
                {allowedMethods.map((method) => (
                  <Chip
                    key={method.id}
                    label={`${method.identifier}${method.edition ? ` · ${method.edition}` : ""}`}
                    color={
                      methodIds.includes(method.id) ? "primary" : "default"
                    }
                    variant={
                      methodIds.includes(method.id) ? "filled" : "outlined"
                    }
                    onClick={() => toggleMethod(method.id)}
                  />
                ))}
              </Stack>
            ) : (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.5 }}
              >
                No methods are associated with this catalog test.
              </Typography>
            )}
          </Box>
          <Divider />
          <CapabilityParameters
            claims={claims}
            onChange={setClaims}
            active={open}
          />
          {declarationChanged && (
            <Alert severity="warning">
              The test, methods or declaration changed. Check that the detailed
              parameters still apply.
              <FormControlLabel
                control={
                  <Checkbox
                    checked={detailsReviewed}
                    onChange={(event) =>
                      setDetailsReviewed(event.target.checked)
                    }
                  />
                }
                label="I reviewed the detailed parameters for this updated capability."
              />
            </Alert>
          )}
          {initial?.atomic_claims?.length && claims.length === 0 ? (
            <Alert severity="info">
              All detailed parameters will be removed. The remaining wording and
              general limits will be used for comparison.
            </Alert>
          ) : null}
          <Accordion
            expanded={detailsOpen}
            onChange={(_, expanded) => setDetailsOpen(expanded)}
            disableGutters
            elevation={0}
            sx={{
              border: "1px solid",
              borderColor: "divider",
              "&:before": { display: "none" },
            }}
          >
            <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
              <Box>
                <Typography fontWeight={600}>
                  Additional wording and notes
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Existing descriptions stay saved. Use the fields above for new
                  ranges and measurements.
                </Typography>
              </Box>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={2}>
                <TextField
                  label="Conditions and scope"
                  value={constraints}
                  onChange={(e) => setConstraints(e.target.value)}
                  multiline
                  minRows={2}
                  fullWidth
                  helperText="Preserve any restrictions or instructions that are not covered by individual fields."
                />
                <TextField
                  label="Measurements and additional capability details"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  multiline
                  minRows={2}
                  fullWidth
                  helperText="Describe what can be measured or reported, not whether a customer's specimen will pass."
                />
              </Stack>
            </AccordionDetails>
          </Accordion>
          <Accordion
            disableGutters
            elevation={0}
            sx={{
              border: "1px solid",
              borderColor: "divider",
              "&:before": { display: "none" },
            }}
          >
            <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
              <Typography variant="body2" fontWeight={600}>
                General operating limits
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Box>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={limitsEnabled}
                      onChange={(e) => setLimitsEnabled(e.target.checked)}
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2" fontWeight={600}>
                        Add structured operating limits
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Use exact numeric bounds, discrete values, or supported
                        phrases.
                      </Typography>
                    </Box>
                  }
                />
                {limitsEnabled && (
                  <Stack spacing={1.25} sx={{ mt: 0.75 }}>
                    {limits.map((limit, index) => (
                      <Box
                        key={index}
                        sx={{
                          p: 1.5,
                          border: "1px solid",
                          borderColor: "divider",
                          borderRadius: 1.5,
                        }}
                      >
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={1}
                          sx={{ mb: 1 }}
                        >
                          <TextField
                            select
                            size="small"
                            label="Parameter"
                            value={limit.parameter}
                            onChange={(e) =>
                              patchLimit(index, {
                                ...asDraft(
                                  freshLimit(e.target.value as Parameter),
                                ),
                                parameter: e.target.value as Parameter,
                              })
                            }
                            sx={{ minWidth: 180 }}
                          >
                            {parameters.map((p) => (
                              <MenuItem key={p} value={p}>
                                {labelFor(p)}
                              </MenuItem>
                            ))}
                          </TextField>
                          <Box sx={{ flex: 1 }} />
                          <IconButton
                            aria-label="Remove limit"
                            size="small"
                            onClick={() =>
                              setLimits((current) =>
                                current.filter((_, i) => i !== index),
                              )
                            }
                          >
                            <DeleteOutlineRoundedIcon fontSize="small" />
                          </IconButton>
                        </Stack>
                        {units[limit.parameter] ? (
                          <Stack
                            direction={{ xs: "column", sm: "row" }}
                            spacing={1}
                          >
                            <TextField
                              size="small"
                              label="Minimum"
                              value={limit.minimum}
                              onChange={(e) =>
                                patchLimit(index, { minimum: e.target.value })
                              }
                              fullWidth
                              placeholder="No minimum"
                            />
                            <TextField
                              size="small"
                              label="Maximum"
                              value={limit.maximum}
                              onChange={(e) =>
                                patchLimit(index, { maximum: e.target.value })
                              }
                              fullWidth
                              placeholder="No maximum"
                            />
                            <TextField
                              size="small"
                              label={`Discrete values (${units[limit.parameter]})`}
                              value={limit.values}
                              onChange={(e) =>
                                patchLimit(index, { values: e.target.value })
                              }
                              fullWidth
                              multiline
                              placeholder="One decimal value per line"
                            />
                          </Stack>
                        ) : (
                          <TextField
                            size="small"
                            label="Supported phrases"
                            value={limit.supported_values}
                            onChange={(e) =>
                              patchLimit(index, {
                                supported_values: e.target.value,
                              })
                            }
                            fullWidth
                            multiline
                            minRows={2}
                            placeholder="One phrase per line"
                          />
                        )}
                      </Box>
                    ))}
                    <Stack direction="row" spacing={1} alignItems="center">
                      <TextField
                        select
                        size="small"
                        label="New limit type"
                        value={limitType}
                        onChange={(e) =>
                          setLimitType(e.target.value as Parameter)
                        }
                      >
                        {parameters.map((p) => (
                          <MenuItem key={p} value={p}>
                            {labelFor(p)}
                          </MenuItem>
                        ))}
                      </TextField>
                      <Button
                        startIcon={<AddRoundedIcon />}
                        onClick={() =>
                          setLimits((current) => [
                            ...current,
                            asDraft(freshLimit(limitType)),
                          ])
                        }
                      >
                        Add limit
                      </Button>
                    </Stack>
                  </Stack>
                )}
              </Box>
            </AccordionDetails>
          </Accordion>
          <Divider />
          <Box>
            <Typography variant="overline" color="text.secondary">
              Rate (optional)
            </Typography>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1}
              sx={{ mt: 1 }}
            >
              <TextField
                label="Price"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                fullWidth
                placeholder="e.g. 125.00"
                inputProps={{ inputMode: "decimal" }}
              />
              <TextField
                label="Currency"
                value={currency}
                onChange={(e) => setCurrency(e.target.value.toUpperCase())}
                fullWidth
                placeholder="USD"
                inputProps={{ maxLength: 3 }}
              />
              <TextField
                label="Pricing basis"
                value={basis}
                onChange={(e) => setBasis(e.target.value)}
                fullWidth
                placeholder="per sample"
              />
            </Stack>
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions
        sx={{
          position: "sticky",
          bottom: 0,
          bgcolor: "background.paper",
          p: 2,
        }}
      >
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={save}
          disabled={saving || !catalog.tests.length}
        >
          {saving ? "Saving…" : "Save capability"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
