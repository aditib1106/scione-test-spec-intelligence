import { useLayoutEffect, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  ButtonBase,
  Chip,
  Collapse,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import DeleteOutlineRoundedIcon from "@mui/icons-material/DeleteOutlineRounded";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import { useResource } from "../api";
import type { AtomicClaim } from "../types";
import { claimDraft, type DraftClaim } from "./labClaimDrafts";

export interface ParameterDefinition {
  id: string;
  label: string;
  description: string;
  value_kind: "number" | "category" | "set" | "boolean";
  rule: string;
  default_unit: string | null;
  default_stage?: string;
  allowed_roles: Array<"condition" | "reporting" | "scope">;
}
interface Props {
  claims: DraftClaim[];
  onChange: (claims: DraftClaim[]) => void;
  active: boolean;
  errors?: Record<string, string | undefined>;
  validationAttempt?: number;
  hiddenClaimIds?: ReadonlySet<string>;
}
const isReporting = (claim: DraftClaim) =>
  ["reporting", "criterion"].includes(claim.kind);

/** Display the draft as entered; this never parses or changes a declaration. */
function draftSummary(claim: DraftClaim) {
  let value = "";
  if (["text", "required"].includes(claim.operator)) {
    value = claim.text_value.trim();
  } else if (claim.operator === "range") {
    const minimum = claim.minimum.trim();
    const maximum = claim.maximum.trim();
    value =
      minimum && maximum
        ? `${minimum}–${maximum}`
        : minimum
          ? `≥ ${minimum}`
          : maximum
            ? `≤ ${maximum}`
            : "";
  } else if (claim.operator === "one_of") {
    value = claim.values.trim();
  } else if (claim.value.trim()) {
    value = `${claim.operator === "=" ? "" : `${claim.operator} `}${claim.value.trim()}${claim.tolerance.trim() ? ` ± ${claim.tolerance.trim()}` : ""}`;
  }
  if (value && claim.unit.trim()) value += ` ${claim.unit.trim()}`;
  return (value || claim.raw_text.trim() || "No value entered").replace(
    /\s+/g,
    " ",
  );
}

export default function CapabilityParameters({
  claims,
  onChange,
  active,
  errors = {},
  validationAttempt = 0,
  hiddenClaimIds,
}: Props) {
  const registry = useResource<{ parameters: ParameterDefinition[] }>(
    active ? "/api/parameters" : null,
  );
  const definitions = registry.data?.parameters ?? [];
  const [expanded, setExpanded] = useState<string | null>(null);
  const [openFields, setOpenFields] = useState<Set<string>>(() => new Set());
  const errorIds = JSON.stringify(
    Object.keys(errors)
      .filter((id) => errors[id])
      .sort(),
  );
  useLayoutEffect(() => {
    if (!active) {
      setOpenFields((current) => (current.size ? new Set() : current));
      setExpanded(null);
      return;
    }
    const identifiers = JSON.parse(errorIds) as string[];
    setOpenFields((current) =>
      identifiers.every((id) => current.has(id))
        ? current
        : new Set([...current, ...identifiers]),
    );
  }, [active, errorIds, validationAttempt]);
  const toggleField = (id: string) =>
    setOpenFields((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const patch = (id: string, changes: Partial<DraftClaim>) =>
    onChange(
      claims.map((claim) =>
        claim.id === id ? { ...claim, ...changes } : claim,
      ),
    );
  const add = (reporting: boolean) => {
    const claim = claimDraft();
    setOpenFields((current) => new Set([...current, claim.id]));
    onChange([
      ...claims,
      { ...claim, kind: reporting ? "reporting" : "condition" },
    ]);
  };
  const selectParameter = (claim: DraftClaim, id: string) => {
    const definition = definitions.find((entry) => entry.id === id);
    if (!definition) {
      patch(claim.id, {
        parameter_id: `custom_${crypto.randomUUID().replaceAll("-", "")}`,
        label: "",
        operator: "text",
        unit: "",
        text_value: "",
        raw_text: "",
        value: "",
        minimum: "",
        maximum: "",
        values: "",
        tolerance: "",
      });
      return;
    }
    patch(claim.id, {
      parameter_id: definition.id,
      label: definition.label,
      stage: definition.default_stage ?? "general",
      kind:
        isReporting(claim) && definition.allowed_roles.includes("reporting")
          ? "reporting"
          : definition.allowed_roles[0],
      operator:
        definition.value_kind === "number"
          ? definition.rule === "lower_is_better"
            ? "="
            : "range"
          : definition.value_kind === "boolean"
            ? "required"
            : "text",
      unit: definition.default_unit ?? "",
      text_value: "",
      raw_text: "",
      value: "",
      minimum: "",
      maximum: "",
      values: "",
      tolerance: "",
    });
  };
  return (
    <Stack spacing={3}>
      {registry.error && (
        <Alert
          severity="info"
          action={
            <Button color="inherit" onClick={registry.refresh}>
              Retry
            </Button>
          }
        >
          Common fields could not be loaded. Saved entries are preserved; you
          can still add a custom field.
        </Alert>
      )}
      {[false, true].map((reporting) => {
        const group = claims.filter(
          (claim) =>
            !hiddenClaimIds?.has(claim.id) && isReporting(claim) === reporting,
        );
        const title = reporting ? "Measurements & reporting" : "Conditions";
        return (
          <Box
            key={title}
            component="section"
            aria-label={title}
            sx={{
              p: 2.25,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 2,
              bgcolor: "background.paper",
            }}
          >
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              spacing={1}
              sx={{ mb: 0.75 }}
            >
              <Typography variant="subtitle1" fontWeight={650}>
                {reporting
                  ? "Measurements & reporting"
                  : "Operating conditions"}
              </Typography>
              <Button
                startIcon={<AddRoundedIcon />}
                size="small"
                onClick={() => add(reporting)}
              >
                {reporting ? "Add measurement" : "Add condition"}
              </Button>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              {reporting
                ? "Measurements your lab can report."
                : "Settings, materials and test preparation."}
            </Typography>
            {!group.length && (
              <Box
                sx={{
                  py: 1,
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  {reporting
                    ? "No measurements recorded yet."
                    : "No conditions recorded yet."}
                </Typography>
              </Box>
            )}
            <Stack spacing={1.5}>
              {group.map((claim, position) => {
                const definition = definitions.find(
                  (entry) => entry.id === claim.parameter_id,
                );
                const numeric = !["text", "required"].includes(claim.operator);
                const valueRequired = claim.availability === "supported";
                const rangeDescriptionId = `${claim.id}-range-description`;
                const advanced = expanded === claim.id;
                const fieldOpen = openFields.has(claim.id);
                const detailsId = `capability-field-${claim.id}`;
                const context = [
                  claim.stage && claim.stage !== "general" ? claim.stage : "",
                  claim.source_context,
                  claim.configuration,
                  claim.sequence ? `Step ${claim.sequence}` : "",
                ]
                  .filter(Boolean)
                  .join(" · ");
                const choices = definitions.filter((entry) =>
                  reporting
                    ? entry.allowed_roles.includes("reporting")
                    : entry.allowed_roles.some((role) => role !== "reporting"),
                );
                return (
                  <Box
                    key={claim.id}
                    component="fieldset"
                    aria-label={`${title} field ${position + 1}`}
                    data-validation-invalid={Boolean(errors[claim.id])}
                    sx={{
                      m: 0,
                      p: 0,
                      minWidth: 0,
                      border: "1px solid",
                      borderColor: errors[claim.id] ? "error.main" : "divider",
                      borderRadius: 1.5,
                      bgcolor: "background.paper",
                    }}
                  >
                    <Stack
                      direction="row"
                      alignItems="center"
                      sx={{ pr: 0.75 }}
                    >
                      <ButtonBase
                        aria-label={`Edit ${claim.label || "new field"}`}
                        aria-expanded={fieldOpen}
                        aria-controls={detailsId}
                        onClick={() => toggleField(claim.id)}
                        sx={{
                          flex: 1,
                          minWidth: 0,
                          p: 1.5,
                          textAlign: "left",
                          justifyContent: "space-between",
                          alignItems: "center",
                          gap: 1,
                          borderRadius: 1.5,
                          "&.Mui-focusVisible": {
                            outline: "2px solid",
                            outlineColor: "primary.main",
                            outlineOffset: -2,
                          },
                        }}
                      >
                        <Box sx={{ minWidth: 0 }}>
                          <Typography variant="body2" fontWeight={650}>
                            {claim.label || "New field"}
                          </Typography>
                          {!fieldOpen && (
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              sx={{
                                mt: 0.25,
                                display: "-webkit-box",
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: "vertical",
                                overflow: "hidden",
                                overflowWrap: "anywhere",
                              }}
                            >
                              {draftSummary(claim)}
                            </Typography>
                          )}
                          {!fieldOpen && context && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{
                                display: "block",
                                mt: 0.25,
                                overflowWrap: "anywhere",
                              }}
                            >
                              {context}
                            </Typography>
                          )}
                        </Box>
                        <Stack
                          direction="row"
                          alignItems="center"
                          gap={0.75}
                          sx={{ flexShrink: 0 }}
                        >
                          {!fieldOpen && claim.availability !== "supported" && (
                            <Typography
                              variant="caption"
                              fontWeight={600}
                              color={
                                claim.availability === "unsupported"
                                  ? "error.main"
                                  : "warning.dark"
                              }
                            >
                              {claim.availability === "unsupported"
                                ? "Not available"
                                : "Not assessed"}
                            </Typography>
                          )}
                          <ExpandMoreRoundedIcon
                            fontSize="small"
                            sx={{
                              color: "text.secondary",
                              transform: fieldOpen ? "rotate(180deg)" : "none",
                              transition: "transform 180ms",
                            }}
                          />
                        </Stack>
                      </ButtonBase>
                      <IconButton
                        size="small"
                        aria-label={`Remove ${claim.label || "field"}`}
                        onClick={() =>
                          onChange(
                            claims.filter((entry) => entry.id !== claim.id),
                          )
                        }
                      >
                        <DeleteOutlineRoundedIcon fontSize="small" />
                      </IconButton>
                    </Stack>
                    <Box id={detailsId}>
                      <Collapse in={fieldOpen} unmountOnExit>
                        <Stack spacing={1.5} sx={{ px: 1.5, pb: 1.5 }}>
                          <Stack
                            direction={{ xs: "column", sm: "row" }}
                            spacing={1.5}
                          >
                            <TextField
                              select
                              fullWidth
                              size="small"
                              label="Field"
                              value={
                                definition
                                  ? definition.id
                                  : claim.parameter_id
                                    ? "custom"
                                    : ""
                              }
                              onChange={(event) =>
                                selectParameter(claim, event.target.value)
                              }
                              helperText={definition?.description}
                            >
                              <MenuItem value="" disabled>
                                Choose a field
                              </MenuItem>
                              {definition &&
                                !choices.some(
                                  (entry) => entry.id === definition.id,
                                ) && (
                                  <MenuItem value={definition.id}>
                                    {definition.label}
                                  </MenuItem>
                                )}
                              {choices.map((entry) => (
                                <MenuItem key={entry.id} value={entry.id}>
                                  {entry.label}
                                </MenuItem>
                              ))}
                              <MenuItem value="custom">Other field</MenuItem>
                            </TextField>
                            {!definition && (
                              <TextField
                                fullWidth
                                size="small"
                                label="Field name"
                                required
                                inputProps={{ "aria-label": "Field name" }}
                                error={
                                  Boolean(errors[claim.id]) &&
                                  !claim.label.trim()
                                }
                                value={claim.label}
                                placeholder="For example: special fixture"
                                onChange={(event) =>
                                  patch(claim.id, {
                                    label: event.target.value,
                                    ...(!claim.parameter_id
                                      ? {
                                          parameter_id: `custom_${crypto.randomUUID().replaceAll("-", "")}`,
                                        }
                                      : {}),
                                  })
                                }
                              />
                            )}
                            <TextField
                              select
                              fullWidth
                              size="small"
                              label="Availability"
                              value={claim.availability}
                              onChange={(event) =>
                                patch(claim.id, {
                                  availability: event.target
                                    .value as AtomicClaim["availability"],
                                })
                              }
                            >
                              <MenuItem value="supported">Available</MenuItem>
                              <MenuItem value="unsupported">
                                Not available
                              </MenuItem>
                              <MenuItem value="unknown">
                                Not yet assessed
                              </MenuItem>
                            </TextField>
                          </Stack>
                          {errors[claim.id] && (
                            <Alert severity="error" sx={{ py: 0 }}>
                              {errors[claim.id]}
                            </Alert>
                          )}
                          {claim.parameter_id && (
                            <>
                              <TextField
                                select
                                fullWidth
                                size="small"
                                label="Value type"
                                value={claim.operator}
                                onChange={(event) =>
                                  patch(claim.id, {
                                    operator: event.target
                                      .value as AtomicClaim["operator"],
                                  })
                                }
                              >
                                {(!definition ||
                                  definition.value_kind === "number") && [
                                  <MenuItem key="range" value="range">
                                    Numeric range
                                  </MenuItem>,
                                  <MenuItem key="=" value="=">
                                    Fixed value / resolution
                                  </MenuItem>,
                                  <MenuItem key="one_of" value="one_of">
                                    Specific numeric settings
                                  </MenuItem>,
                                  <MenuItem key="<=" value="<=">
                                    At most (≤)
                                  </MenuItem>,
                                  <MenuItem key=">=" value=">=">
                                    At least (≥)
                                  </MenuItem>,
                                  <MenuItem key="<" value="<">
                                    Less than (&lt;)
                                  </MenuItem>,
                                  <MenuItem key=">" value=">">
                                    Greater than (&gt;)
                                  </MenuItem>,
                                ]}
                                {(!definition ||
                                  definition.value_kind !== "number" ||
                                  ["text", "required"].includes(
                                    claim.operator,
                                  )) && [
                                  <MenuItem key="text" value="text">
                                    {definition?.value_kind === "set"
                                      ? "Supported items"
                                      : "Named value / description"}
                                  </MenuItem>,
                                  <MenuItem key="required" value="required">
                                    Included resource / output
                                  </MenuItem>,
                                ]}
                              </TextField>
                              {numeric && claim.operator === "range" && (
                                <Typography
                                  id={rangeDescriptionId}
                                  variant="caption"
                                  color="text.secondary"
                                >
                                  {valueRequired
                                    ? "Range * — enter a minimum, a maximum, or both."
                                    : "Range is optional when this capability is not available or not yet assessed."}
                                </Typography>
                              )}
                              {numeric ? (
                                <Stack
                                  direction={{ xs: "column", sm: "row" }}
                                  spacing={1}
                                >
                                  {claim.operator === "range" ? (
                                    <>
                                      <TextField
                                        fullWidth
                                        size="small"
                                        label="Minimum"
                                        inputProps={{
                                          "aria-describedby":
                                            rangeDescriptionId,
                                        }}
                                        value={claim.minimum}
                                        onChange={(event) =>
                                          patch(claim.id, {
                                            minimum: event.target.value,
                                          })
                                        }
                                      />
                                      <TextField
                                        fullWidth
                                        size="small"
                                        label="Maximum"
                                        inputProps={{
                                          "aria-describedby":
                                            rangeDescriptionId,
                                        }}
                                        value={claim.maximum}
                                        onChange={(event) =>
                                          patch(claim.id, {
                                            maximum: event.target.value,
                                          })
                                        }
                                      />
                                    </>
                                  ) : claim.operator === "one_of" ? (
                                    <TextField
                                      fullWidth
                                      size="small"
                                      label="Specific values"
                                      required={valueRequired}
                                      inputProps={{
                                        "aria-label": "Specific values",
                                      }}
                                      value={claim.values}
                                      placeholder="20, 60, 85"
                                      onChange={(event) =>
                                        patch(claim.id, {
                                          values: event.target.value,
                                        })
                                      }
                                    />
                                  ) : (
                                    <TextField
                                      fullWidth
                                      size="small"
                                      label="Value"
                                      required={valueRequired}
                                      inputProps={{ "aria-label": "Value" }}
                                      value={claim.value}
                                      onChange={(event) =>
                                        patch(claim.id, {
                                          value: event.target.value,
                                        })
                                      }
                                    />
                                  )}
                                  <TextField
                                    fullWidth
                                    size="small"
                                    label="Unit"
                                    value={claim.unit}
                                    onChange={(event) =>
                                      patch(claim.id, {
                                        unit: event.target.value,
                                      })
                                    }
                                  />
                                </Stack>
                              ) : (
                                definition?.value_kind !== "boolean" && (
                                  <TextField
                                    fullWidth
                                    size="small"
                                    label={
                                      definition?.value_kind === "set"
                                        ? "Supported items (one per line)"
                                        : "Declared capability"
                                    }
                                    required={
                                      valueRequired && claim.operator === "text"
                                    }
                                    inputProps={{
                                      "aria-label":
                                        definition?.value_kind === "set"
                                          ? "Supported items (one per line)"
                                          : "Declared capability",
                                    }}
                                    value={claim.text_value}
                                    multiline={definition?.value_kind === "set"}
                                    minRows={
                                      definition?.value_kind === "set"
                                        ? 2
                                        : undefined
                                    }
                                    onChange={(event) =>
                                      patch(claim.id, {
                                        text_value: event.target.value,
                                      })
                                    }
                                  />
                                )
                              )}
                            </>
                          )}
                          {claim.kind === "criterion" && (
                            <Alert severity="info">
                              This is a saved acceptance limit. Keep it only if
                              it describes an existing lab declaration.
                            </Alert>
                          )}
                          {!definition && claim.parameter_id && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              A custom field stays distinct until its meaning is
                              confirmed during comparison.
                            </Typography>
                          )}
                          <Accordion
                            expanded={advanced}
                            onChange={(_, value) =>
                              setExpanded(value ? claim.id : null)
                            }
                            disableGutters
                            elevation={0}
                            sx={{
                              "&:before": { display: "none" },
                              bgcolor: "transparent",
                            }}
                          >
                            <AccordionSummary
                              expandIcon={<ExpandMoreRoundedIcon />}
                              sx={{ px: 0, minHeight: 32 }}
                            >
                              <Stack
                                direction="row"
                                alignItems="center"
                                spacing={1}
                              >
                                <Typography variant="body2">
                                  Context and wording
                                </Typography>
                                {claim.source_context && (
                                  <Chip
                                    size="small"
                                    label={claim.source_context}
                                  />
                                )}
                              </Stack>
                            </AccordionSummary>
                            <AccordionDetails sx={{ px: 0, pb: 0 }}>
                              <Stack spacing={1.5}>
                                <Stack
                                  direction={{ xs: "column", sm: "row" }}
                                  spacing={1}
                                >
                                  <TextField
                                    fullWidth
                                    size="small"
                                    label="Stage"
                                    value={claim.stage}
                                    helperText="Use the same stage for related settings."
                                    onChange={(event) =>
                                      patch(claim.id, {
                                        stage: event.target.value,
                                      })
                                    }
                                  />
                                  <TextField
                                    fullWidth
                                    size="small"
                                    label="Material or context"
                                    value={claim.source_context}
                                    onChange={(event) =>
                                      patch(claim.id, {
                                        source_context: event.target.value,
                                      })
                                    }
                                  />
                                </Stack>
                                <Stack
                                  direction={{ xs: "column", sm: "row" }}
                                  spacing={1}
                                >
                                  <TextField
                                    fullWidth
                                    size="small"
                                    label="Setup / configuration"
                                    value={claim.configuration}
                                    onChange={(event) =>
                                      patch(claim.id, {
                                        configuration: event.target.value,
                                      })
                                    }
                                  />
                                  <TextField
                                    fullWidth
                                    size="small"
                                    label="Step order"
                                    value={claim.sequence}
                                    onChange={(event) =>
                                      patch(claim.id, {
                                        sequence: event.target.value,
                                      })
                                    }
                                  />
                                  {claim.operator === "=" && (
                                    <TextField
                                      fullWidth
                                      size="small"
                                      label="Tolerance (±)"
                                      value={claim.tolerance}
                                      onChange={(event) =>
                                        patch(claim.id, {
                                          tolerance: event.target.value,
                                        })
                                      }
                                    />
                                  )}
                                </Stack>
                                <TextField
                                  fullWidth
                                  size="small"
                                  label="Declaration wording"
                                  value={claim.raw_text}
                                  multiline
                                  helperText="Optional for new fields; wording is generated from the values. Existing wording is retained."
                                  onChange={(event) =>
                                    patch(claim.id, {
                                      raw_text: event.target.value,
                                    })
                                  }
                                />
                              </Stack>
                            </AccordionDetails>
                          </Accordion>
                        </Stack>
                      </Collapse>
                    </Box>
                  </Box>
                );
              })}
            </Stack>
          </Box>
        );
      })}
    </Stack>
  );
}
