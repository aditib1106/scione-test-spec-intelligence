import { useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
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
}
const isReporting = (claim: DraftClaim) =>
  ["reporting", "criterion"].includes(claim.kind);

export default function CapabilityParameters({
  claims,
  onChange,
  active,
}: Props) {
  const registry = useResource<{ parameters: ParameterDefinition[] }>(
    active ? "/api/parameters" : null,
  );
  const definitions = registry.data?.parameters ?? [];
  const [expanded, setExpanded] = useState<string | null>(null);
  const patch = (id: string, changes: Partial<DraftClaim>) =>
    onChange(
      claims.map((claim) =>
        claim.id === id ? { ...claim, ...changes } : claim,
      ),
    );
  const add = (reporting: boolean) => {
    const claim = claimDraft();
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
          (claim) => isReporting(claim) === reporting,
        );
        const title = reporting ? "Criteria / reporting" : "Conditions";
        return (
          <Box key={title} component="section" aria-label={title}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              spacing={1}
              sx={{ mb: 0.75 }}
            >
              <Typography variant="h6" fontWeight={650}>
                {title}
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
                ? "What your lab can measure and report. This does not promise that a specimen will pass."
                : "Record the ranges, settings, materials and resources your lab supports."}
            </Typography>
            {!group.length && (
              <Box
                sx={{
                  p: 2,
                  bgcolor: "grey.50",
                  border: "1px dashed",
                  borderColor: "divider",
                  borderRadius: 1.5,
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  {reporting
                    ? "No measurements recorded yet."
                    : "No individual conditions recorded yet."}{" "}
                  Missing information will remain unknown during comparison.
                </Typography>
              </Box>
            )}
            <Stack spacing={1.5}>
              {group.map((claim, position) => {
                const definition = definitions.find(
                  (entry) => entry.id === claim.parameter_id,
                );
                const numeric = !["text", "required"].includes(claim.operator);
                const advanced = expanded === claim.id;
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
                    sx={{
                      m: 0,
                      p: 2,
                      minWidth: 0,
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 1.5,
                    }}
                  >
                    <Stack spacing={1.5}>
                      <Stack
                        direction="row"
                        alignItems="center"
                        justifyContent="space-between"
                      >
                        <Typography fontWeight={600}>
                          {claim.label || "New field"}
                        </Typography>
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
                          <MenuItem value="unsupported">Not available</MenuItem>
                          <MenuItem value="unknown">Not yet assessed</MenuItem>
                        </TextField>
                      </Stack>
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
                                  patch(claim.id, { unit: event.target.value })
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
                          This is a saved acceptance limit. Keep it only if it
                          describes an existing lab declaration.
                        </Alert>
                      )}
                      {!definition && claim.parameter_id && (
                        <Typography variant="caption" color="text.secondary">
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
                              <Chip size="small" label={claim.source_context} />
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
                                  patch(claim.id, { stage: event.target.value })
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
