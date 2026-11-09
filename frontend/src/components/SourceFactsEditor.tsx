import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { request } from "../api";
import type {
  Requirement,
  RequirementValue,
  SpecificationData,
} from "../types";

interface Props {
  data: SpecificationData;
  selected: Requirement;
  onSaved: (data: SpecificationData) => void;
}

export default function SourceFactsEditor({ data, selected, onSaved }: Props) {
  const fullScreen = useMediaQuery(useTheme().breakpoints.down("sm"));
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Requirement>(selected);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);
  const patch = (values: Partial<Requirement>) =>
    setDraft((old) => ({ ...old, ...values }));

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
      const corrected = fieldsChanged ? { ...draft, atomic_claims: [] } : draft;
      const next = await request<SpecificationData>(
        `/api/specifications/${data.document_id}/facts`,
        {
          method: "POST",
          body: JSON.stringify({
            run_id: data.run.run_id,
            expected_version: data.facts_version ?? 0,
            expected_review_version: data.review?.version ?? 0,
            requirements: data.run.extraction.requirements.map((item) =>
              item.id === selected.id ? corrected : item,
            ),
            note: note.trim(),
          }),
        },
      );
      onSaved(next);
      setOpen(false);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to save corrections.",
      );
    } finally {
      setSaving(false);
    }
  }

  function validationError() {
    if (!draft.original_test_name.trim())
      return "Enter the test name stated in the source.";
    if (draft.requested_methods.some((method) => !method.code.trim()))
      return "Enter a method identifier for each method, or remove the empty method.";
    for (const [key, heading] of [
      ["conditions", "Conditions"],
      ["acceptance_criteria", "Criteria / reporting"],
    ] as const) {
      for (const [index, value] of draft[key].entries()) {
        if (!value.name.trim())
          return `Add a parameter name for ${heading.toLowerCase()} ${index + 1}.`;
        if (!value.raw_value.trim())
          return `Add the customer requirement for ${heading.toLowerCase()} ${index + 1}, or remove the empty entry.`;
      }
    }
    if (!draft.evidence.length)
      return "Keep at least one source quotation for this test.";
    for (const [index, evidence] of draft.evidence.entries()) {
      if (
        !Number.isInteger(evidence.page) ||
        evidence.page < 1 ||
        evidence.page > data.document.page_count
      )
        return `Enter a whole source page from 1 to ${data.document.page_count} for quotation ${index + 1}.`;
      if (!evidence.quote.trim())
        return `Add the source quotation for evidence ${index + 1}.`;
    }
    if (!note.trim()) return "Briefly explain the correction before saving.";
    if (JSON.stringify(draft) === JSON.stringify(selected))
      return "Change at least one extracted field before saving a correction.";
    return "";
  }

  function fields(key: "conditions" | "acceptance_criteria", heading: string) {
    const values = draft[key];
    const update = (index: number, change: Partial<RequirementValue>) =>
      patch({
        [key]: values.map((value, at) =>
          at === index ? { ...value, ...change } : value,
        ),
      });
    return (
      <Stack spacing={1.5}>
        <Typography variant="subtitle1" fontWeight={700}>
          {heading}
        </Typography>
        {values.map((value, index) => (
          <Box
            key={index}
            component="fieldset"
            aria-label={`${heading} ${index + 1}`}
            sx={{
              m: 0,
              minWidth: 0,
              p: 1.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 2,
            }}
          >
            <Stack direction="row" spacing={1} alignItems="center">
              <TextField
                label="Parameter name"
                required
                value={value.name}
                size="small"
                fullWidth
                onChange={(e) => update(index, { name: e.target.value })}
              />
              <IconButton
                aria-label={`Remove ${key === "conditions" ? "condition" : "criterion"} ${index + 1}`}
                onClick={() =>
                  patch({ [key]: values.filter((_, at) => at !== index) })
                }
              >
                <CloseIcon />
              </IconButton>
            </Stack>
            <TextField
              label="Customer requirement"
              required
              multiline
              minRows={2}
              value={value.raw_value}
              fullWidth
              sx={{ mt: 1.5 }}
              onChange={(e) => update(index, { raw_value: e.target.value })}
            />
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1.5}
              sx={{ mt: 1.5 }}
            >
              <TextField
                label="Unit (optional)"
                value={value.unit ?? ""}
                size="small"
                onChange={(e) =>
                  update(index, { unit: e.target.value || null })
                }
              />
              <TextField
                label="Stage or context (optional)"
                value={value.context ?? ""}
                size="small"
                fullWidth
                onChange={(e) =>
                  update(index, { context: e.target.value || null })
                }
              />
            </Stack>
          </Box>
        ))}
        <Button
          variant="text"
          sx={{ alignSelf: "flex-start" }}
          onClick={() =>
            patch({
              [key]: [
                ...values,
                {
                  name:
                    heading === "Conditions"
                      ? "Customer condition"
                      : "Customer criterion",
                  raw_value: "",
                  unit: null,
                  context: null,
                },
              ],
            })
          }
        >
          Add {heading === "Conditions" ? "condition" : "criterion"}
        </Button>
      </Stack>
    );
  }

  return (
    <>
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Button
          size="small"
          variant="outlined"
          onClick={() => {
            setDraft(structuredClone(selected));
            setNote("");
            setError("");
            setOpen(true);
          }}
        >
          Correct extracted content
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
        onClose={() => {
          if (!saving) setOpen(false);
        }}
        maxWidth="md"
        fullWidth
        fullScreen={fullScreen}
        scroll="paper"
      >
        <DialogTitle>Correct extracted content</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2.5}>
            <Alert severity="info">
              Check corrections against the PDF. The original model output is
              kept. Saving creates a corrected draft and clears this test's
              earlier confirmation. Confirm it again after checking.
            </Alert>
            {error && (
              <Alert severity="error" ref={errorRef} tabIndex={-1}>
                {error}
              </Alert>
            )}
            <TextField
              label="Test name"
              required
              value={draft.original_test_name}
              onChange={(e) => patch({ original_test_name: e.target.value })}
              fullWidth
            />
            <Typography fontWeight={700}>
              Methods stated in the source
            </Typography>
            {draft.requested_methods.map((method, index) => (
              <Stack
                key={index}
                component="fieldset"
                aria-label={`Source method ${index + 1}`}
                sx={{ m: 0, p: 0, minWidth: 0, border: 0 }}
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
              >
                <TextField
                  label="Method identifier"
                  required
                  value={method.code}
                  fullWidth
                  onChange={(e) =>
                    patch({
                      requested_methods: draft.requested_methods.map(
                        (item, at) =>
                          at === index
                            ? { ...item, code: e.target.value }
                            : item,
                      ),
                    })
                  }
                />
                <TextField
                  label="Method use (optional)"
                  helperText="For example: required or alternative."
                  multiline
                  minRows={1}
                  value={method.role ?? ""}
                  fullWidth
                  onChange={(e) =>
                    patch({
                      requested_methods: draft.requested_methods.map(
                        (item, at) =>
                          at === index
                            ? { ...item, role: e.target.value || null }
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
            <Button
              sx={{ alignSelf: "flex-start" }}
              onClick={() =>
                patch({
                  requested_methods: [...draft.requested_methods, { code: "" }],
                })
              }
            >
              Add method
            </Button>
            <Divider />
            {fields("conditions", "Conditions")}
            <Divider />
            {fields("acceptance_criteria", "Criteria / reporting")}
            <Divider />
            <Typography fontWeight={700}>Source evidence</Typography>
            {draft.evidence.map((evidence, index) => (
              <Stack
                key={index}
                component="fieldset"
                aria-label={`Source evidence ${index + 1}`}
                spacing={1}
                sx={{
                  m: 0,
                  p: 1.5,
                  minWidth: 0,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 2,
                }}
              >
                <TextField
                  type="number"
                  label="Source page"
                  required
                  value={evidence.page}
                  onChange={(e) =>
                    patch({
                      evidence: draft.evidence.map((item, at) =>
                        at === index
                          ? { ...item, page: Number(e.target.value) }
                          : item,
                      ),
                    })
                  }
                  inputProps={{ min: 1, max: data.document.page_count }}
                />
                <TextField
                  label="Source quotation"
                  required
                  multiline
                  minRows={2}
                  value={evidence.quote}
                  onChange={(e) =>
                    patch({
                      evidence: draft.evidence.map((item, at) =>
                        at === index
                          ? { ...item, quote: e.target.value }
                          : item,
                      ),
                    })
                  }
                />
              </Stack>
            ))}
            <TextField
              label="Reason for correction"
              multiline
              minRows={2}
              required
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions
          sx={{
            p: 2,
            position: "sticky",
            bottom: 0,
            bgcolor: "background.paper",
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Button disabled={saving} onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? "Saving…" : "Save corrected draft"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
