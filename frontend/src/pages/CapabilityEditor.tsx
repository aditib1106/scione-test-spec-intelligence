import { useEffect, useMemo, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
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
  CapabilitySubmission,
  Catalog,
  CustomLabMethod,
  OperatingLimit,
} from "../types";
import { ApiError } from "../api";
import CapabilityParameters from "../components/CapabilityParameters";
import {
  claimDraft,
  savedClaim,
  type DraftClaim,
} from "../components/labClaimDrafts";
import { capabilityDetailSx } from "../components/LabCapabilityDetails";

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
  onSave: (submission: CapabilitySubmission) => Promise<void>;
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
  const [customTest, setCustomTest] = useState(false);
  const [customTestId, setCustomTestId] = useState<string | undefined>();
  const [customTestName, setCustomTestName] = useState("");
  const [customTestDescription, setCustomTestDescription] = useState("");
  const [customTestAliases, setCustomTestAliases] = useState<string[]>([]);
  const [methodIds, setMethodIds] = useState<string[]>([]);
  const [customMethods, setCustomMethods] = useState<CustomLabMethod[]>([]);
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
    const selectedTest = initial?.canonical_test_id ?? "";
    const privateTest = catalog.tests.find(
      (item) => item.id === selectedTest && item.id.startsWith("labtest_"),
    );
    setTestId(selectedTest);
    setCustomTest(Boolean(privateTest));
    setCustomTestId(privateTest?.id);
    setCustomTestName(privateTest?.name ?? "");
    setCustomTestDescription(privateTest?.description ?? "");
    setCustomTestAliases(privateTest?.aliases.map((alias) => alias.text) ?? []);
    setMethodIds(
      initial?.method_ids.filter((id) => !id.startsWith("labmethod_")) ?? [],
    );
    setCustomMethods(
      catalog.methods
        .filter(
          (method) =>
            initial?.method_ids.includes(method.id) &&
            method.id.startsWith("labmethod_"),
        )
        .map((method) => ({
          id: method.id,
          name: method.name,
          identifier:
            method.identifier === method.name ? "" : method.identifier,
          aliases: method.aliases.map((alias) => alias.text),
        })),
    );
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
  }, [
    testId,
    customTest,
    customTestName,
    customTestDescription,
    methodIds,
    customMethods,
    constraints,
    notes,
    limitsEnabled,
    limits,
  ]);

  const test = catalog.tests.find((item) => item.id === testId);
  const allowedMethods = useMemo(
    () =>
      catalog.methods.filter(
        (item) =>
          (customTest && !customTestId) || test?.method_ids.includes(item.id),
      ),
    [catalog.methods, customTest, customTestId, test],
  );
  const patchLimit = (index: number, patch: Partial<DraftLimit>) =>
    setLimits((current) =>
      current.map((limit, i) => (i === index ? { ...limit, ...patch } : limit)),
    );
  const declarationChanged =
    Boolean(initial?.atomic_claims?.length) &&
    ((customTest && !customTestId) ||
      testId !== initial?.canonical_test_id ||
      JSON.stringify(
        [
          ...methodIds,
          ...customMethods.flatMap((method) => (method.id ? [method.id] : [])),
        ].sort(),
      ) !== JSON.stringify([...(initial?.method_ids ?? [])].sort()) ||
      customMethods.some((method) => {
        const original = catalog.methods.find((item) => item.id === method.id);
        return (
          !original ||
          method.name.trim() !== original.name ||
          (method.identifier?.trim() || method.name.trim()) !==
            original.identifier
        );
      }) ||
      (customTest &&
        (customTestName.trim() !== test?.name ||
          customTestDescription.trim() !== test?.description)) ||
      constraints.trim() !== (initial?.declared_constraints ?? "") ||
      notes.trim() !== (initial?.notes ?? "") ||
      JSON.stringify(limitsEnabled ? limits.map(fromDraft) : []) !==
        JSON.stringify(initial?.structured_limits ?? []));

  async function save() {
    setError("");
    if (customTest ? !customTestName.trim() : !testId) {
      setError(
        customTest
          ? "Enter your test name."
          : "Choose a test name or create a custom test.",
      );
      return;
    }
    if (customMethods.some((method) => !method.name.trim())) {
      setError(
        "Enter a name for each custom method, or remove the empty method.",
      );
      return;
    }
    if (declarationChanged && !detailsReviewed) {
      setDetailsOpen(true);
      setError(
        "Check the saved conditions and measurements after changing the test, methods or description, then confirm below.",
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
          : "Check the conditions and measurements.",
      );
      return;
    }
    setSaving(true);
    const id = initial?.id ?? `cap_${crypto.randomUUID().replaceAll("-", "")}`;
    const next: Capability = {
      id,
      canonical_test_id: customTest ? (customTestId ?? "custom") : testId,
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
    if (Boolean(price.trim()) !== Boolean(currency.trim() && basis.trim())) {
      setError(
        "Add a currency and pricing basis with the price, or leave all three blank.",
      );
      setSaving(false);
      return;
    }
    try {
      await onSave({
        capability: next,
        ...(customTest
          ? {
              custom_test: {
                ...(customTestId ? { id: customTestId } : {}),
                name: customTestName.trim(),
                description: customTestDescription.trim(),
                ...(customTestAliases.length
                  ? { aliases: customTestAliases }
                  : {}),
              },
            }
          : {}),
        ...(customMethods.length
          ? {
              custom_methods: customMethods.map((method) => ({
                ...(method.id ? { id: method.id } : {}),
                name: method.name.trim(),
                ...(method.identifier?.trim()
                  ? { identifier: method.identifier.trim() }
                  : {}),
                ...(method.aliases?.length ? { aliases: method.aliases } : {}),
              })),
            }
          : {}),
      });
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
          Choose a test, then record what your lab can perform and report.
        </Typography>
      </DialogTitle>
      <DialogContent dividers sx={{ bgcolor: "grey.50" }}>
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
          <Box
            component="section"
            aria-label="Test and methods"
            sx={{
              p: 2.25,
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              borderRadius: 2,
            }}
          >
            <Typography variant="overline" color="text.secondary">
              Test item
            </Typography>
            <ToggleButtonGroup
              exclusive
              value={customTest ? "custom" : "catalog"}
              onChange={(_, value: string | null) => {
                if (!value) return;
                setCustomTest(value === "custom");
                if (
                  value === "custom" &&
                  !initial?.canonical_test_id.startsWith("labtest_")
                ) {
                  setCustomTestId(undefined);
                }
                setMethodIds([]);
                setCustomMethods([]);
              }}
              aria-label="Test source"
              size="small"
              sx={{ display: "flex", width: "fit-content", mt: 1, mb: 2 }}
            >
              <ToggleButton value="catalog">Choose a test</ToggleButton>
              <ToggleButton value="custom">Create custom test</ToggleButton>
            </ToggleButtonGroup>
            {customTest ? (
              <Stack spacing={1.5}>
                <TextField
                  label="Custom test name"
                  value={customTestName}
                  onChange={(event) => setCustomTestName(event.target.value)}
                  required
                  fullWidth
                  placeholder="e.g. In-house coating flexibility"
                />
                <TextField
                  label="Test description (optional)"
                  value={customTestDescription}
                  onChange={(event) =>
                    setCustomTestDescription(event.target.value)
                  }
                  multiline
                  minRows={2}
                  fullWidth
                  helperText="This test is saved for your lab. The shared reference catalog stays unchanged."
                />
              </Stack>
            ) : (
              <Autocomplete
                options={catalog.tests}
                value={test ?? null}
                getOptionLabel={(item) => item.name}
                renderOption={(optionProps, item) => {
                  const { key, ...other } = optionProps;
                  const scope = item.id.startsWith("labtest_")
                    ? "Private to this lab"
                    : "Shared reference";
                  return (
                    <li
                      key={key}
                      {...other}
                      aria-label={item.name}
                      aria-description={scope}
                    >
                      <Box>
                        <Typography variant="body2">{item.name}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {scope}
                        </Typography>
                      </Box>
                    </li>
                  );
                }}
                isOptionEqualToValue={(option, value) => option.id === value.id}
                filterOptions={(options, { inputValue }) => {
                  const needle = inputValue.trim().toLocaleLowerCase();
                  return options.filter((item) =>
                    `${item.name} ${item.aliases.map((alias) => alias.text).join(" ")} ${catalog.methods
                      .filter((method) => item.method_ids.includes(method.id))
                      .map((method) => method.identifier)
                      .join(" ")}`
                      .toLocaleLowerCase()
                      .includes(needle),
                  );
                }}
                onChange={(_, selected) => {
                  setTestId(selected?.id ?? "");
                  setMethodIds([]);
                  setCustomMethods([]);
                }}
                noOptionsText="No matching test. Use Create custom test to add your own."
                renderInput={(input) => (
                  <TextField
                    {...input}
                    label="Test name"
                    placeholder="Search by name, alias or method"
                    helperText={
                      test
                        ? test.id.startsWith("labtest_")
                          ? "Private to this lab"
                          : "Shared test reference; your capability is saved for your lab only."
                        : undefined
                    }
                  />
                )}
              />
            )}
            <Box sx={{ mt: 2 }}>
              <Autocomplete
                multiple
                disableCloseOnSelect
                options={allowedMethods.filter(
                  (method) =>
                    !customMethods.some((custom) => custom.id === method.id),
                )}
                value={allowedMethods.filter((method) =>
                  methodIds.includes(method.id),
                )}
                getOptionLabel={(method) =>
                  method.identifier && method.identifier !== method.name
                    ? `${method.identifier} · ${method.name}`
                    : method.name
                }
                isOptionEqualToValue={(option, value) => option.id === value.id}
                onChange={(_, selected) =>
                  setMethodIds(selected.map((method) => method.id))
                }
                filterOptions={(options, { inputValue }) => {
                  const needle = inputValue.trim().toLocaleLowerCase();
                  return options.filter((method) =>
                    `${method.identifier} ${method.name} ${method.aliases.map((alias) => alias.text).join(" ")}`
                      .toLocaleLowerCase()
                      .includes(needle),
                  );
                }}
                renderOption={(optionProps, method, { selected }) => {
                  const { key, ...other } = optionProps;
                  return (
                    <li key={key} {...other}>
                      <Checkbox
                        size="small"
                        checked={selected}
                        sx={{ mr: 1 }}
                      />
                      <Box>
                        <Typography variant="body2" fontWeight={600}>
                          {method.identifier || method.name}
                        </Typography>
                        {method.name !== method.identifier && (
                          <Typography variant="caption" color="text.secondary">
                            {method.name}
                          </Typography>
                        )}
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: "block" }}
                        >
                          {method.id.startsWith("labmethod_")
                            ? "Private to this lab"
                            : "Shared reference"}
                        </Typography>
                      </Box>
                    </li>
                  );
                }}
                renderInput={(input) => (
                  <TextField
                    {...input}
                    label="Supported methods (optional)"
                    placeholder="Search methods"
                  />
                )}
                noOptionsText="No matching method. Add a custom method below."
                disabled={!customTest && !testId}
              />
              <Stack spacing={1.25} sx={{ mt: customMethods.length ? 1.5 : 0 }}>
                {customMethods.map((method, index) => (
                  <Stack
                    key={method.id ?? `new-${index}`}
                    direction={{ xs: "column", sm: "row" }}
                    spacing={1}
                    alignItems={{ sm: "center" }}
                    sx={{ p: 1.5, bgcolor: "grey.50", borderRadius: 1.5 }}
                  >
                    <TextField
                      label={`Custom method ${index + 1}`}
                      required
                      value={method.name}
                      onChange={(event) =>
                        setCustomMethods((current) =>
                          current.map((item, i) =>
                            i === index
                              ? { ...item, name: event.target.value }
                              : item,
                          ),
                        )
                      }
                      size="small"
                      fullWidth
                    />
                    <TextField
                      label="Code (optional)"
                      value={method.identifier ?? ""}
                      onChange={(event) =>
                        setCustomMethods((current) =>
                          current.map((item, i) =>
                            i === index
                              ? { ...item, identifier: event.target.value }
                              : item,
                          ),
                        )
                      }
                      size="small"
                      sx={{ width: { xs: "100%", sm: 200 }, flexShrink: 0 }}
                    />
                    <IconButton
                      aria-label={`Remove custom method ${index + 1}`}
                      onClick={() =>
                        setCustomMethods((current) =>
                          current.filter((_, i) => i !== index),
                        )
                      }
                      size="small"
                    >
                      <DeleteOutlineRoundedIcon fontSize="small" />
                    </IconButton>
                  </Stack>
                ))}
              </Stack>
              <Button
                startIcon={<AddRoundedIcon />}
                size="small"
                onClick={() =>
                  setCustomMethods((current) => [
                    ...current,
                    { name: "", identifier: "" },
                  ])
                }
                sx={{ mt: 1 }}
                disabled={!customTest && !testId}
              >
                Add custom method
              </Button>
              {customMethods.length > 0 && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 0.75 }}
                >
                  Custom methods are private to this lab.
                </Typography>
              )}
            </Box>
          </Box>
          <CapabilityParameters
            claims={claims}
            onChange={setClaims}
            active={open}
          />
          {declarationChanged && (
            <Alert severity="warning">
              The test, methods or description changed. Check that the saved
              conditions and measurements still apply.
              <FormControlLabel
                control={
                  <Checkbox
                    checked={detailsReviewed}
                    onChange={(event) =>
                      setDetailsReviewed(event.target.checked)
                    }
                  />
                }
                label="I checked the conditions and measurements for this capability."
              />
            </Alert>
          )}
          {initial?.atomic_claims?.length && claims.length === 0 ? (
            <Alert severity="info">
              All individual conditions and measurements will be removed. Any
              remaining descriptions and operating limits will still be saved.
            </Alert>
          ) : null}
          <Accordion
            expanded={detailsOpen}
            onChange={(_, expanded) => setDetailsOpen(expanded)}
            disableGutters
            elevation={0}
            sx={capabilityDetailSx}
          >
            <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
              <Box>
                <Typography fontWeight={600}>
                  Additional wording and notes
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Extra instructions that do not fit an individual field.
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
          {Boolean(initial?.structured_limits.length) && (
            <Accordion disableGutters elevation={0} sx={capabilityDetailSx}>
              <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                <Typography variant="body2" fontWeight={600}>
                  Additional operating limits
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
                          Include these operating limits
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          Limits previously recorded for this test.
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
          )}
          <Box
            component="section"
            aria-label="Pricing"
            sx={{
              p: 2.25,
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              borderRadius: 2,
            }}
          >
            <Typography variant="subtitle1" fontWeight={650}>
              Reference price
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mt: 0.35 }}
            >
              Optional. Leave all three fields blank if this test is not priced.
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
          gap: 1,
        }}
      >
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button variant="contained" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save capability"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
