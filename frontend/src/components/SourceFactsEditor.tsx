import { useEffect, useRef, useState } from "react";
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
  Tab,
  Tabs,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import { request, useResource } from "../api";
import type {
  AtomicClaim,
  Requirement,
  RequirementValue,
  SpecificationData,
} from "../types";
import type { ParameterDefinition } from "./CapabilityParameters";
import {
  claimContext,
  claimValue,
  isReviewParameter,
  isSamplePlanClaim,
  type SourceReviewClaim,
} from "./sourceReviewClaims";

interface Props {
  data: SpecificationData;
  selected: Requirement;
  onSaved: (data: SpecificationData) => void;
  adding?: boolean;
  editRequest?: { claimId: string; nonce: number } | null;
  onEditRequestHandled?: () => void;
}
type Section = "overview" | "condition" | "criterion" | "planning" | "source";
const numericPattern = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
const cleanClaim = (value: SourceReviewClaim): AtomicClaim => ({
  id: value.id,
  parameter_id: value.parameter_id,
  label: value.label,
  kind: value.kind,
  stage: value.stage,
  operator: value.operator,
  value: value.value,
  minimum: value.minimum,
  maximum: value.maximum,
  tolerance: value.tolerance,
  values: [...value.values],
  unit: value.unit,
  text_value: value.text_value,
  availability: value.availability,
  raw_text: value.raw_text,
  source_pages: [...value.source_pages],
  source_context: value.source_context ?? null,
  normalization_state: value.normalization_state,
  configuration: value.configuration,
  sequence: value.sequence,
});

export default function SourceFactsEditor({
  data,
  selected,
  onSaved,
  adding = false,
  editRequest,
  onEditRequestHandled,
}: Props) {
  const fullScreen = useMediaQuery(useTheme().breakpoints.down("sm"));
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Requirement>(selected);
  const [section, setSection] = useState<Section>("overview");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [rebuildDetails, setRebuildDetails] = useState(false);
  const [wordingRebuildAcknowledged, setWordingRebuildAcknowledged] =
    useState(false);
  const [selectedAtomId, setSelectedAtomId] = useState<string | null>(null);
  const [atomEdits, setAtomEdits] = useState<Record<string, AtomicClaim>>({});
  const [selectedRawIndex, setSelectedRawIndex] = useState(0);
  const [evidenceIndex, setEvidenceIndex] = useState(0);
  const [rawEditExpanded, setRawEditExpanded] = useState(false);
  const registry = useResource<{
    parameters: (ParameterDefinition & { aliases?: string[] })[];
  }>(open ? "/api/parameters" : null);
  const sourceAtoms: SourceReviewClaim[] =
    data.normalized_requirements?.[selected.id]?.claims ??
    selected.atomic_claims ??
    [];
  const canRebuildDetails = !adding && Boolean(selected.atomic_claims?.length);
  const originalWordingChanged = [
    "conditions",
    "acceptance_criteria",
    "applicability",
    "special_instructions",
  ].some(
    (key) =>
      JSON.stringify(draft[key as keyof Requirement]) !==
      JSON.stringify(selected[key as keyof Requirement]),
  );
  const wordingRebuildRequired = canRebuildDetails && originalWordingChanged;
  const wordingRebuildAllowed = wordingRebuildAcknowledged || rebuildDetails;
  const mounted = useRef(true);
  const lastRequest = useRef<number | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);
  useEffect(() => {
    if (!wordingRebuildRequired) setWordingRebuildAcknowledged(false);
  }, [wordingRebuildRequired]);

  function begin(claimId?: string) {
    setDraft(structuredClone(selected));
    setNote("");
    setError("");
    setRebuildDetails(false);
    setWordingRebuildAcknowledged(false);
    setAtomEdits({});
    setSelectedRawIndex(0);
    setEvidenceIndex(0);
    const atom = sourceAtoms.find((item) => item.id === claimId);
    const textOnly = atom && !isReviewParameter(atom);
    setSelectedAtomId(textOnly ? null : (claimId ?? null));
    setRawEditExpanded(Boolean(textOnly));
    if (textOnly) {
      const items =
        atom.kind === "condition"
          ? selected.conditions
          : selected.acceptance_criteria;
      const normalize = (text: string) => text.replace(/\s+/g, " ").trim();
      const raw = normalize(atom.raw_text);
      const index = items.findIndex((item) => {
        const value = normalize(item.raw_value);
        return value.includes(raw) || raw.includes(value);
      });
      setSelectedRawIndex(Math.max(0, index));
    }
    setSection(
      atom
        ? isSamplePlanClaim(atom)
          ? "planning"
          : atom.kind === "condition"
            ? "condition"
            : "criterion"
        : "overview",
    );
    setOpen(true);
  }
  useEffect(() => {
    if (
      editRequest &&
      lastRequest.current !== editRequest.nonce &&
      sourceAtoms.some((item) => item.id === editRequest.claimId)
    ) {
      lastRequest.current = editRequest.nonce;
      begin(editRequest.claimId);
      onEditRequestHandled?.();
    }
  }, [editRequest?.nonce]);
  const patch = (values: Partial<Requirement>) =>
    setDraft((old) => ({ ...old, ...values }));
  const patchAtom = (identifier: string, changes: Partial<AtomicClaim>) => {
    const original =
      atomEdits[identifier] ??
      sourceAtoms.find((item) => item.id === identifier);
    if (!original) return;
    setAtomEdits((current) => ({
      ...current,
      [identifier]: { ...cleanClaim(original), ...changes },
    }));
  };
  const activeAtoms = sourceAtoms.filter(
    (claim) =>
      isReviewParameter(claim) &&
      (section === "planning"
        ? isSamplePlanClaim(claim)
        : section === "condition"
          ? claim.kind === "condition" && !isSamplePlanClaim(claim)
          : ["criterion", "reporting"].includes(claim.kind)),
  );
  const activeAtom = selectedAtomId
    ? (atomEdits[selectedAtomId] ??
      sourceAtoms.find((claim) => claim.id === selectedAtomId))
    : null;
  const rawKey = section === "condition" ? "conditions" : "acceptance_criteria";
  const rawValues = draft[rawKey];

  function validationError() {
    if (!draft.original_test_name.trim())
      return "Enter the test name stated in the source.";
    if (draft.requested_methods.some((method) => !method.code.trim()))
      return "Enter each method identifier, or remove the empty method.";
    for (const [key, heading] of [
      ["conditions", "condition"],
      ["acceptance_criteria", "criterion"],
    ] as const) {
      for (const [index, value] of draft[key].entries()) {
        if (!value.name.trim())
          return `Name ${heading} ${index + 1} so it can be understood.`;
        if (!value.raw_value.trim())
          return `Enter ${heading} ${index + 1}, or remove the empty entry.`;
      }
    }
    if (!draft.evidence.length)
      return "Keep at least one source quotation for this test.";
    for (const evidence of draft.evidence) {
      if (
        !Number.isInteger(evidence.page) ||
        evidence.page < 1 ||
        evidence.page > data.document.page_count
      )
        return `Select a source page from 1 to ${data.document.page_count}.`;
      if (!evidence.quote.trim())
        return "Keep the exact source quotation for every source page.";
    }
    for (const value of Object.values(atomEdits)) {
      if (!value.label.trim()) return "Name each edited parameter.";
      if (!value.parameter_id)
        return "Choose the parameter that the source describes, or choose Other parameter.";
      const nums =
        value.operator === "range"
          ? [value.minimum, value.maximum].filter((entry) => entry !== null)
          : value.operator === "one_of"
            ? value.values
            : ["text", "required"].includes(value.operator)
              ? []
              : [value.value];
      if (
        !["text", "required"].includes(value.operator) &&
        (!nums.length ||
          nums.some((entry) => !entry || !numericPattern.test(entry)))
      )
        return `Enter a valid number for ${value.label}.`;
      if (
        ["text", "required"].includes(value.operator) &&
        !value.text_value?.trim()
      )
        return `Enter the requirement for ${value.label}.`;
      if (!value.source_pages.length || !value.raw_text.trim())
        return `Keep the source passage for ${value.label}.`;
      if (
        value.sequence !== null &&
        (!Number.isInteger(value.sequence) || value.sequence < 1)
      )
        return "Use a positive whole number for the step, or leave it blank.";
      if (
        value.tolerance !== null &&
        (!numericPattern.test(value.tolerance) || Number(value.tolerance) < 0)
      )
        return "Enter a valid, non-negative tolerance.";
    }
    if (
      !adding &&
      JSON.stringify(draft) === JSON.stringify(selected) &&
      !Object.keys(atomEdits).length &&
      !(canRebuildDetails && rebuildDetails)
    )
      return "Change at least one extracted field before saving a correction.";
    const rawChanged = [
      "conditions",
      "acceptance_criteria",
      "applicability",
      "special_instructions",
    ].some(
      (key) =>
        JSON.stringify(draft[key as keyof Requirement]) !==
        JSON.stringify(selected[key as keyof Requirement]),
    );
    if (rawChanged && Object.keys(atomEdits).length)
      return "Save the original wording changes first, then edit the refreshed parameters.";
    if (wordingRebuildRequired && !wordingRebuildAllowed)
      return "Confirm that this test's detailed fields can be rebuilt before saving.";
    return "";
  }
  async function save() {
    if (!data.run) return;
    const invalid = validationError();
    if (invalid) {
      setError(invalid);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const fieldsChanged = [
        "conditions",
        "acceptance_criteria",
        "applicability",
        "special_instructions",
      ].some(
        (key) =>
          JSON.stringify(draft[key as keyof Requirement]) !==
          JSON.stringify(selected[key as keyof Requirement]),
      );
      let atoms = [...(draft.atomic_claims ?? [])];
      for (const [sourceId, value] of Object.entries(atomEdits)) {
        const original = sourceAtoms.find((item) => item.id === sourceId);
        const nativeIds = new Set(original?.source_atom_ids ?? [sourceId]);
        const existing = atoms.findIndex((item) => nativeIds.has(item.id));
        const updated = {
          ...cleanClaim(value),
          normalization_state: "reviewed" as const,
          // A guard's derived unknown state is not the native source declaration.
          availability:
            existing >= 0
              ? atoms[existing].availability
              : ("supported" as const),
        };
        if (existing >= 0) {
          const retainedId = atoms[existing].id;
          atoms = atoms.filter(
            (item, index) => !nativeIds.has(item.id) || index === existing,
          );
          atoms[existing] = { ...updated, id: retainedId };
        } else
          atoms.push({
            ...updated,
            id: `human_atom_${crypto.randomUUID().replaceAll("-", "")}`,
          });
      }
      const corrected =
        fieldsChanged || (canRebuildDetails && rebuildDetails)
          ? { ...draft, atomic_claims: [] }
          : Object.keys(atomEdits).length
            ? { ...draft, atomic_claims: atoms }
            : draft;
      const next = await request<SpecificationData>(
        `/api/specifications/${data.document_id}/${adding ? "requirements" : "facts"}`,
        {
          method: "POST",
          body: JSON.stringify({
            run_id: data.run.run_id,
            expected_version: data.facts_version ?? 0,
            expected_review_version: data.review?.version ?? 0,
            ...(adding
              ? { requirement: corrected }
              : {
                  requirements: data.run.extraction.requirements.map((item) =>
                    item.id === selected.id ? corrected : item,
                  ),
                }),
            note:
              note.trim() ||
              (adding
                ? "User added a test."
                : "User edited extracted test information."),
          }),
        },
      );
      if (!mounted.current) return;
      onSaved(next);
      setOpen(false);
    } catch (reason) {
      if (mounted.current)
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to save corrections.",
        );
    } finally {
      if (mounted.current) setSaving(false);
    }
  }
  function selectSection(value: Section) {
    setSection(value);
    setSelectedRawIndex(0);
    setRawEditExpanded(false);
    const first = sourceAtoms.find(
      (claim) =>
        isReviewParameter(claim) &&
        (value === "planning"
          ? isSamplePlanClaim(claim)
          : value === "condition"
            ? claim.kind === "condition" && !isSamplePlanClaim(claim)
            : ["criterion", "reporting"].includes(claim.kind)),
    );
    setSelectedAtomId(
      value === "condition" || value === "criterion" || value === "planning"
        ? (first?.id ?? null)
        : null,
    );
  }
  function parameterForm() {
    if (!activeAtom) return null;
    const identifier = activeAtom.id;
    const fold = (value: string) =>
      value
        .normalize("NFKC")
        .toLowerCase()
        .replace(/[\s_-]+/g, " ")
        .trim();
    const identity = activeAtom.parameter_id
      ? fold(activeAtom.parameter_id)
      : "";
    const definition = registry.data?.parameters.find(
      (item) =>
        identity &&
        [item.id, ...(item.aliases ?? [])].some(
          (name) => fold(name) === identity,
        ),
    );
    const numeric = !["text", "required"].includes(activeAtom.operator);
    const numberField = (
      key: "value" | "minimum" | "maximum" | "tolerance",
      label: string,
    ) => (
      <TextField
        size="small"
        fullWidth
        label={label}
        sx={{ minWidth: 0 }}
        value={activeAtom[key] ?? ""}
        onChange={(event) =>
          patchAtom(identifier, { [key]: event.target.value || null })
        }
      />
    );
    return (
      <Stack
        gap={1.25}
        sx={{
          p: 0,
          bgcolor: "#fff",
          borderRadius: 0,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          gap={1}
        >
          <Typography fontWeight={700} variant="body2">
            Edit one requirement
          </Typography>
          <Chip
            size="small"
            label={
              isSamplePlanClaim(activeAtom)
                ? "Sample plan / instruction"
                : activeAtom.kind === "condition"
                  ? "Condition"
                  : "Acceptance criteria / reporting"
            }
          />
        </Stack>
        <TextField
          select
          label="Parameter"
          size="small"
          value={definition?.id ?? "__other__"}
          onChange={(event) => {
            const choice = registry.data?.parameters.find(
              (item) => item.id === event.target.value,
            );
            patchAtom(
              identifier,
              choice
                ? { parameter_id: choice.id, label: choice.label }
                : {
                    parameter_id: `custom_${crypto.randomUUID().replaceAll("-", "")}`,
                    label: activeAtom.label,
                  },
            );
          }}
        >
          {(registry.data?.parameters ?? []).map((item) => (
            <MenuItem key={item.id} value={item.id}>
              {item.label}
            </MenuItem>
          ))}
          <MenuItem value="__other__">Other parameter</MenuItem>
        </TextField>
        {!definition && (
          <TextField
            size="small"
            label="Parameter name"
            value={activeAtom.label}
            onChange={(event) =>
              patchAtom(identifier, {
                label: event.target.value,
                parameter_id:
                  activeAtom.parameter_id ??
                  `custom_${crypto.randomUUID().replaceAll("-", "")}`,
              })
            }
          />
        )}
        <Stack direction={{ xs: "column", sm: "row" }} gap={1.25}>
          <TextField
            select
            size="small"
            label="Requirement"
            value={activeAtom.operator}
            sx={{ width: { xs: "100%", sm: 150 }, flexShrink: 0 }}
            onChange={(event) => {
              const operator = event.target.value as AtomicClaim["operator"];
              patchAtom(identifier, {
                operator,
                value: ["range", "one_of", "text", "required"].includes(
                  operator,
                )
                  ? null
                  : activeAtom.value,
                minimum: operator === "range" ? activeAtom.minimum : null,
                maximum: operator === "range" ? activeAtom.maximum : null,
                tolerance: operator === "=" ? activeAtom.tolerance : null,
                values: operator === "one_of" ? activeAtom.values : [],
                text_value: ["text", "required"].includes(operator)
                  ? activeAtom.text_value || activeAtom.raw_text
                  : null,
              });
            }}
          >
            <MenuItem value="=">Exactly</MenuItem>
            <MenuItem value="range">Between</MenuItem>
            <MenuItem value=">=">At least</MenuItem>
            <MenuItem value="<=">At most</MenuItem>
            <MenuItem value=">">Greater than</MenuItem>
            <MenuItem value="<">Less than</MenuItem>
            <MenuItem value="one_of">
              {activeAtom.kind === "condition"
                ? "Required settings"
                : "Allowed values"}
            </MenuItem>
            <MenuItem value="text">Written requirement</MenuItem>
            <MenuItem value="required">Required</MenuItem>
          </TextField>
          {activeAtom.operator === "range" ? (
            <>
              {numberField("minimum", "Minimum")}
              {numberField("maximum", "Maximum")}
            </>
          ) : activeAtom.operator === "one_of" ? (
            <TextField
              size="small"
              fullWidth
              label="Values (separated by commas)"
              value={activeAtom.values.join(", ")}
              onChange={(event) =>
                patchAtom(identifier, {
                  values: event.target.value
                    .split(",")
                    .map((item) => item.trim()),
                })
              }
            />
          ) : numeric ? (
            numberField("value", "Value")
          ) : (
            <TextField
              size="small"
              fullWidth
              label="Customer requirement"
              multiline
              minRows={2}
              maxRows={5}
              value={activeAtom.text_value ?? ""}
              onChange={(event) =>
                patchAtom(identifier, { text_value: event.target.value })
              }
            />
          )}
          {activeAtom.operator === "=" &&
            activeAtom.tolerance !== null &&
            numberField("tolerance", "Tolerance ±")}
          {numeric && (
            <TextField
              size="small"
              label="Unit"
              value={activeAtom.unit ?? ""}
              sx={{ width: { xs: "100%", sm: 100 }, flexShrink: 0 }}
              onChange={(event) =>
                patchAtom(identifier, { unit: event.target.value || null })
              }
            />
          )}
        </Stack>
        <Accordion
          disableGutters
          elevation={0}
          sx={{ bgcolor: "transparent", "&:before": { display: "none" } }}
        >
          <AccordionSummary
            expandIcon={<ExpandMoreIcon />}
            sx={{ px: 0, minHeight: 32 }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="caption" fontWeight={600}>
                Stage & applicability
              </Typography>
              {claimContext(activeAtom) && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{
                    display: "block",
                    overflowWrap: "anywhere",
                    lineHeight: 1.5,
                  }}
                >
                  {claimContext(activeAtom)}
                </Typography>
              )}
            </Box>
          </AccordionSummary>
          <AccordionDetails sx={{ px: 0 }}>
            <Stack gap={1.5}>
              <TextField
                select
                label="Requirement type"
                size="small"
                value={activeAtom.kind}
                onChange={(event) =>
                  patchAtom(identifier, {
                    kind: event.target.value as AtomicClaim["kind"],
                  })
                }
              >
                <MenuItem value="condition">Test condition</MenuItem>
                <MenuItem value="criterion">Acceptance criterion</MenuItem>
                <MenuItem value="reporting">Reported result</MenuItem>
                <MenuItem value="scope">Where the test applies</MenuItem>
                <MenuItem value="instruction">Additional instruction</MenuItem>
              </TextField>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: {
                    xs: "1fr",
                    sm: "minmax(0, 1fr) 110px",
                  },
                  gap: 1.25,
                }}
              >
                <TextField
                  size="small"
                  fullWidth
                  label="Test stage"
                  value={
                    activeAtom.stage === "general"
                      ? ""
                      : activeAtom.stage.replaceAll("_", " ")
                  }
                  placeholder="For example: after cycling"
                  onChange={(event) =>
                    patchAtom(identifier, {
                      stage: event.target.value || "general",
                    })
                  }
                />
                <TextField
                  size="small"
                  label="Step (optional)"
                  type="number"
                  value={activeAtom.sequence ?? ""}
                  inputProps={{ min: 1 }}
                  onChange={(event) =>
                    patchAtom(identifier, {
                      sequence: event.target.value
                        ? Number(event.target.value)
                        : null,
                    })
                  }
                />
              </Box>
              {activeAtom.operator === "=" &&
                activeAtom.tolerance === null &&
                numberField("tolerance", "Tolerance ± (optional)")}
              <TextField
                size="small"
                label="Applies to"
                multiline
                maxRows={4}
                value={activeAtom.source_context ?? ""}
                onChange={(event) =>
                  patchAtom(identifier, {
                    source_context: event.target.value || null,
                  })
                }
              />
              <TextField
                size="small"
                label="Setup or material group"
                value={activeAtom.configuration ?? ""}
                onChange={(event) =>
                  patchAtom(identifier, {
                    configuration: event.target.value || null,
                  })
                }
              />
            </Stack>
          </AccordionDetails>
        </Accordion>
        <Box
          sx={{
            p: 1.25,
            bgcolor: "#f5f8fc",
            borderRadius: 1,
            borderLeft: "3px solid #90acd9",
          }}
        >
          <Typography variant="caption" fontWeight={650}>
            Source · Page {activeAtom.source_pages.join(", ")}
          </Typography>
          <Typography
            variant="body2"
            sx={{
              mt: 0.5,
              lineHeight: 1.6,
              whiteSpace: "pre-wrap",
              maxHeight: 110,
              overflowY: "auto",
            }}
          >
            {activeAtom.raw_text}
          </Typography>
        </Box>
      </Stack>
    );
  }
  function rawFields() {
    const row = rawValues[selectedRawIndex];
    const update = (changes: Partial<RequirementValue>) =>
      patch({
        [rawKey]: rawValues.map((item, index) =>
          index === selectedRawIndex ? { ...item, ...changes } : item,
        ),
      });
    return (
      <Stack gap={1.5}>
        <Stack direction="row" gap={1} alignItems="center">
          <TextField
            select
            size="small"
            fullWidth
            label="Source entry"
            value={
              rawValues.length
                ? Math.min(selectedRawIndex, rawValues.length - 1)
                : ""
            }
            onChange={(event) =>
              setSelectedRawIndex(Number(event.target.value))
            }
          >
            {rawValues.map((item, index) => (
              <MenuItem key={index} value={index}>
                {index + 1}. {item.name || "Unnamed requirement"}
              </MenuItem>
            ))}
          </TextField>
          <Button
            startIcon={<AddRoundedIcon />}
            onClick={() => {
              patch({
                [rawKey]: [
                  ...rawValues,
                  { name: "", raw_value: "", unit: null, context: null },
                ],
              });
              setSelectedRawIndex(rawValues.length);
            }}
          >
            Add
          </Button>
        </Stack>
        {row && (
          <Stack
            component="fieldset"
            aria-label={`${section === "condition" ? "Conditions" : "Acceptance criteria / reporting"} ${selectedRawIndex + 1}`}
            gap={1.5}
            sx={{ m: 0, p: 0, minWidth: 0, border: 0 }}
          >
            <Stack direction="row" gap={1}>
              <TextField
                size="small"
                fullWidth
                label="Parameter name"
                placeholder="For example: Temperature"
                value={row.name}
                onChange={(event) => update({ name: event.target.value })}
              />
              <IconButton
                aria-label={`Remove ${section === "condition" ? "condition" : "criterion"} ${selectedRawIndex + 1}`}
                onClick={() => {
                  patch({
                    [rawKey]: rawValues.filter(
                      (_, index) => index !== selectedRawIndex,
                    ),
                  });
                  setSelectedRawIndex(Math.max(0, selectedRawIndex - 1));
                }}
              >
                <CloseIcon />
              </IconButton>
            </Stack>
            <TextField
              label="Customer requirement"
              multiline
              minRows={3}
              maxRows={8}
              fullWidth
              value={row.raw_value}
              onChange={(event) => update({ raw_value: event.target.value })}
            />
            <Stack direction={{ xs: "column", sm: "row" }} gap={1.25}>
              <TextField
                size="small"
                label="Unit (optional)"
                value={row.unit ?? ""}
                onChange={(event) =>
                  update({ unit: event.target.value || null })
                }
              />
              <TextField
                size="small"
                fullWidth
                label="Stage or context (optional)"
                value={row.context ?? ""}
                onChange={(event) =>
                  update({ context: event.target.value || null })
                }
              />
            </Stack>
          </Stack>
        )}
      </Stack>
    );
  }
  return (
    <>
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Button
          size="small"
          variant="outlined"
          aria-label={adding ? "Add missing test" : "Correct extracted content"}
          onClick={() => begin()}
        >
          {adding ? "Add missing test" : "Edit test"}
        </Button>
        {data.facts_revision?.changed_requirement_ids.includes(selected.id) && (
          <Chip
            size="small"
            color="info"
            variant="outlined"
            label="Corrected draft"
          />
        )}
      </Stack>
      <Dialog
        open={open}
        aria-labelledby="source-editor-title"
        onClose={() => {
          if (!saving) setOpen(false);
        }}
        maxWidth="md"
        fullWidth
        fullScreen={fullScreen}
        scroll="paper"
        PaperProps={{
          sx: {
            height: fullScreen
              ? "100dvh"
              : activeAtom && !rawEditExpanded
                ? "auto"
                : "min(790px, 92dvh)",
            maxHeight: fullScreen ? "100dvh" : "92dvh",
            borderRadius: fullScreen ? 0 : "14px",
          },
        }}
      >
        <DialogTitle id="source-editor-heading" sx={{ pb: 1.25 }}>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
          >
            <Box sx={{ minWidth: 0, pr: 1 }}>
              <Typography
                component="span"
                id="source-editor-title"
                fontSize={20}
                fontWeight={750}
              >
                {adding ? "Add missing test" : "Correct extracted content"}
              </Typography>
              {!adding && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 0.25 }}
                  noWrap
                  title={selected.original_test_name}
                >
                  {selected.original_test_name}
                </Typography>
              )}
            </Box>
            <IconButton
              aria-label="Close editor"
              disabled={saving}
              onClick={() => setOpen(false)}
            >
              <CloseIcon />
            </IconButton>
          </Stack>
        </DialogTitle>
        <Tabs
          value={section}
          onChange={(_, value) => selectSection(value)}
          variant={
            sourceAtoms.some(isSamplePlanClaim) ? "scrollable" : "fullWidth"
          }
          scrollButtons="auto"
          aria-label="Edit test sections"
          sx={{
            px: { xs: 0, sm: 2 },
            borderBottom: "1px solid #e0e7f0",
            flexShrink: 0,
          }}
        >
          <Tab value="overview" label="Test & method" />
          <Tab value="condition" label="Conditions" />
          <Tab value="criterion" label="Acceptance criteria" />
          {sourceAtoms.some(isSamplePlanClaim) && (
            <Tab value="planning" label="Sample plan" />
          )}
          <Tab value="source" label="Source evidence" />
        </Tabs>
        <DialogContent sx={{ pt: 2, bgcolor: "#fff", minHeight: 0 }}>
          <Stack spacing={1.5}>
            {error && (
              <Alert severity="error" ref={errorRef} tabIndex={-1}>
                {error}
              </Alert>
            )}
            {section === "overview" && (
              <Stack gap={2}>
                <TextField
                  label="Test name"
                  required
                  value={draft.original_test_name}
                  onChange={(event) =>
                    patch({ original_test_name: event.target.value })
                  }
                  fullWidth
                  multiline
                  maxRows={3}
                />
                <Box>
                  <Typography fontWeight={700} variant="body2" sx={{ mb: 1 }}>
                    Methods stated in the source
                  </Typography>
                  <Stack gap={1}>
                    {draft.requested_methods.map((method, index) => (
                      <Stack
                        key={index}
                        component="fieldset"
                        aria-label={`Source method ${index + 1}`}
                        direction={{ xs: "column", sm: "row" }}
                        gap={1}
                        sx={{ m: 0, p: 0, border: 0, minWidth: 0 }}
                      >
                        <TextField
                          size="small"
                          label="Method identifier"
                          required
                          value={method.code}
                          fullWidth
                          onChange={(event) =>
                            patch({
                              requested_methods: draft.requested_methods.map(
                                (item, at) =>
                                  at === index
                                    ? { ...item, code: event.target.value }
                                    : item,
                              ),
                            })
                          }
                        />
                        <IconButton
                          aria-label={`Remove method ${index + 1}`}
                          onClick={() =>
                            patch({
                              requested_methods: draft.requested_methods.filter(
                                (_, at) => at !== index,
                              ),
                            })
                          }
                        >
                          <CloseIcon />
                        </IconButton>
                      </Stack>
                    ))}
                  </Stack>
                  <Button
                    size="small"
                    startIcon={<AddRoundedIcon />}
                    onClick={() =>
                      patch({
                        requested_methods: [
                          ...draft.requested_methods,
                          { code: "" },
                        ],
                      })
                    }
                    sx={{ mt: 1 }}
                  >
                    Add method
                  </Button>
                  {draft.requested_methods.length > 0 && (
                    <Accordion
                      disableGutters
                      elevation={0}
                      sx={{ mt: 1, "&:before": { display: "none" } }}
                    >
                      <AccordionSummary
                        expandIcon={<ExpandMoreIcon />}
                        sx={{ px: 0, minHeight: 34 }}
                      >
                        <Typography variant="caption" color="text.secondary">
                          Method use (optional)
                        </Typography>
                      </AccordionSummary>
                      <AccordionDetails sx={{ px: 0 }}>
                        <Stack gap={1.5}>
                          {draft.requested_methods.map((method, index) => (
                            <Box key={index}>
                              <Typography
                                variant="caption"
                                fontWeight={600}
                                sx={{ display: "block", mb: 0.5 }}
                              >
                                {method.code || `Method ${index + 1}`}
                              </Typography>
                              <TextField
                                size="small"
                                label="Method use (optional)"
                                value={method.role ?? ""}
                                fullWidth
                                onChange={(event) =>
                                  patch({
                                    requested_methods:
                                      draft.requested_methods.map((item, at) =>
                                        at === index
                                          ? {
                                              ...item,
                                              role: event.target.value || null,
                                            }
                                          : item,
                                      ),
                                  })
                                }
                              />
                            </Box>
                          ))}
                        </Stack>
                      </AccordionDetails>
                    </Accordion>
                  )}
                </Box>
                {canRebuildDetails && (
                  <Accordion
                    disableGutters
                    elevation={0}
                    sx={{
                      border: "1px solid #e0e7f0",
                      borderRadius: "8px !important",
                      "&:before": { display: "none" },
                    }}
                  >
                    <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                      <Typography variant="body2" fontWeight={600}>
                        Detailed parameters
                      </Typography>
                    </AccordionSummary>
                    <AccordionDetails>
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={rebuildDetails}
                            onChange={(event) =>
                              setRebuildDetails(event.target.checked)
                            }
                          />
                        }
                        label="Rebuild detailed parameters from these reviewed fields"
                      />
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        display="block"
                      >
                        Rebuild the detailed requirements from the corrected
                        wording. The original extraction stays available.
                      </Typography>
                    </AccordionDetails>
                  </Accordion>
                )}
              </Stack>
            )}
            {(section === "condition" ||
              section === "criterion" ||
              section === "planning") && (
              <>
                {activeAtoms.length > 0 && (
                  <>
                    <TextField
                      select
                      size="small"
                      fullWidth
                      label="Choose requirement"
                      value={selectedAtomId ?? ""}
                      onChange={(event) =>
                        setSelectedAtomId(event.target.value)
                      }
                      SelectProps={{
                        renderValue: (id) => {
                          const original = activeAtoms.find(
                            (item) => item.id === id,
                          );
                          const value =
                            original && (atomEdits[original.id] ?? original);
                          if (!value) return "Choose a requirement";
                          const stage =
                            value.stage !== "general"
                              ? ` · ${value.stage.replaceAll("_", " ")}`
                              : "";
                          return `${value.label}${stage}`;
                        },
                        MenuProps: { PaperProps: { sx: { maxHeight: 340 } } },
                      }}
                    >
                      {activeAtoms.map((original) => {
                        const value = atomEdits[original.id] ?? original;
                        return (
                          <MenuItem
                            key={original.id}
                            value={original.id}
                            sx={{ whiteSpace: "normal", py: 1 }}
                          >
                            <Box sx={{ minWidth: 0 }}>
                              <Typography variant="body2" fontWeight={600}>
                                {value.label}
                              </Typography>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                sx={{
                                  display: "-webkit-box",
                                  WebkitLineClamp: 2,
                                  WebkitBoxOrient: "vertical",
                                  overflow: "hidden",
                                  overflowWrap: "anywhere",
                                }}
                              >
                                {claimValue(value)}
                                {claimContext(value)
                                  ? ` · ${claimContext(value)}`
                                  : ""}
                              </Typography>
                            </Box>
                          </MenuItem>
                        );
                      })}
                    </TextField>
                    <Divider />
                    {parameterForm()}
                    {section !== "planning" && (
                      <Accordion
                        disableGutters
                        elevation={0}
                        expanded={rawEditExpanded}
                        onChange={(_, expanded) => {
                          if (expanded && Object.keys(atomEdits).length) {
                            setError(
                              "Save your parameter corrections before changing the original wording.",
                            );
                            return;
                          }
                          setRawEditExpanded(expanded);
                        }}
                        sx={{ "&:before": { display: "none" } }}
                      >
                        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                          <Typography variant="body2">
                            Edit original wording
                          </Typography>
                        </AccordionSummary>
                        <AccordionDetails>{rawFields()}</AccordionDetails>
                      </Accordion>
                    )}
                  </>
                )}
                {!activeAtoms.length && section !== "planning" && rawFields()}
              </>
            )}
            {section === "source" && (
              <Stack gap={1.5}>
                <Typography variant="body2" color="text.secondary">
                  Keep an exact quotation that can be found on the selected PDF
                  page.
                </Typography>
                <TextField
                  select
                  size="small"
                  label="Quoted passage"
                  value={Math.min(evidenceIndex, draft.evidence.length - 1)}
                  onChange={(event) =>
                    setEvidenceIndex(Number(event.target.value))
                  }
                >
                  {draft.evidence.map((item, index) => (
                    <MenuItem key={index} value={index}>
                      Page {item.page} · Passage {index + 1}
                    </MenuItem>
                  ))}
                </TextField>
                {draft.evidence[evidenceIndex] && (
                  <Stack
                    component="fieldset"
                    aria-label={`Source evidence ${evidenceIndex + 1}`}
                    gap={1.5}
                    sx={{ m: 0, p: 0, minWidth: 0, border: 0 }}
                  >
                    <TextField
                      size="small"
                      type="number"
                      label="Source page"
                      required
                      value={draft.evidence[evidenceIndex].page}
                      inputProps={{ min: 1, max: data.document.page_count }}
                      onChange={(event) =>
                        patch({
                          evidence: draft.evidence.map((item, index) =>
                            index === evidenceIndex
                              ? { ...item, page: Number(event.target.value) }
                              : item,
                          ),
                        })
                      }
                    />
                    <TextField
                      label="Source quotation"
                      required
                      multiline
                      minRows={5}
                      maxRows={12}
                      value={draft.evidence[evidenceIndex].quote}
                      onChange={(event) =>
                        patch({
                          evidence: draft.evidence.map((item, index) =>
                            index === evidenceIndex
                              ? { ...item, quote: event.target.value }
                              : item,
                          ),
                        })
                      }
                    />
                  </Stack>
                )}
              </Stack>
            )}
          </Stack>
        </DialogContent>
        <Divider />
        <Box
          sx={{ px: { xs: 2, sm: 3 }, pt: 1.5, bgcolor: "#fff", flexShrink: 0 }}
        >
          {wordingRebuildRequired && (
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              <Typography variant="body2">
                Changing original wording rebuilds this test’s detailed fields.
                Review them again after saving.
              </Typography>
              <FormControlLabel
                sx={{ mt: 0.5, mb: -0.5 }}
                control={
                  <Checkbox
                    size="small"
                    checked={wordingRebuildAllowed}
                    onChange={(event) => {
                      setWordingRebuildAcknowledged(event.target.checked);
                      if (!event.target.checked) setRebuildDetails(false);
                    }}
                  />
                }
                label={
                  <Typography variant="body2">
                    Rebuild this test’s detailed fields
                  </Typography>
                }
              />
            </Alert>
          )}
          <Accordion
            disableGutters
            elevation={0}
            sx={{ "&:before": { display: "none" } }}
          >
            <AccordionSummary
              expandIcon={<ExpandMoreIcon />}
              sx={{ px: 0, minHeight: 32 }}
            >
              <Typography variant="caption" color="text.secondary">
                Add a review note (optional)
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ px: 0, pt: 0 }}>
              <TextField
                size="small"
                fullWidth
                label={
                  adding
                    ? "Reason for adding this test (optional)"
                    : "Reason for correction (optional)"
                }
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Briefly describe what you corrected."
              />
            </AccordionDetails>
          </Accordion>
        </Box>
        <DialogActions
          sx={{ px: { xs: 2, sm: 3 }, py: 1.5, bgcolor: "#fff", flexShrink: 0 }}
        >
          <Button disabled={saving} onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={
              saving || (wordingRebuildRequired && !wordingRebuildAllowed)
            }
            onClick={() => void save()}
          >
            {saving
              ? "Saving…"
              : adding
                ? "Add test"
                : wordingRebuildRequired
                  ? "Save & rebuild details"
                  : "Save corrected draft"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
