import type { AtomicClaim } from "../types";

export type DraftClaim = {
  original?: AtomicClaim;
  id: string;
  label: string;
  parameter_id: string;
  kind: AtomicClaim["kind"];
  stage: string;
  operator: AtomicClaim["operator"];
  value: string;
  minimum: string;
  maximum: string;
  tolerance: string;
  values: string;
  unit: string;
  text_value: string;
  raw_text: string;
  source_context: string;
  configuration: string;
  sequence: string;
  availability: AtomicClaim["availability"];
};
export const claimDraft = (claim?: AtomicClaim): DraftClaim => ({
  original: claim,
  id: claim?.id ?? `claim_${crypto.randomUUID().replaceAll("-", "")}`,
  label: claim?.label ?? "",
  parameter_id: claim?.parameter_id ?? "",
  kind: claim?.kind ?? "condition",
  stage: claim?.stage ?? "general",
  operator: claim?.operator ?? "range",
  value: claim?.value ?? "",
  minimum: claim?.minimum ?? "",
  maximum: claim?.maximum ?? "",
  tolerance: claim?.tolerance ?? "",
  values: claim?.values.join(", ") ?? "",
  unit: claim?.unit ?? "",
  text_value: claim?.text_value ?? "",
  raw_text: claim?.raw_text ?? "",
  source_context: claim?.source_context ?? "",
  configuration: claim?.configuration ?? "",
  sequence: claim?.sequence?.toString() ?? "",
  availability: claim?.availability ?? "supported",
});
const numericPattern = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
export function savedClaim(draft: DraftClaim): AtomicClaim {
  const numeric = (value: string): string | null => {
    const trimmed = value.trim();
    if (!trimmed) return null;
    if (!numericPattern.test(trimmed) || !Number.isFinite(Number(trimmed))) {
      throw new Error(
        `Enter a valid number for ${draft.label || "the parameter"}.`,
      );
    }
    return trimmed;
  };
  const isNumeric = !["text", "required"].includes(draft.operator);
  const generatedOriginal =
    draft.original &&
    draft.original.raw_text === declarationWording(claimDraft(draft.original));
  const wording =
    generatedOriginal && draft.raw_text === draft.original?.raw_text
      ? declarationWording(draft)
      : draft.raw_text.trim() || declarationWording(draft);
  if (!draft.label.trim()) {
    throw new Error("Choose a field and give each custom field a name.");
  }
  if (draft.sequence && !/^[1-9]\d*$/.test(draft.sequence)) {
    throw new Error(
      "Step order must be a positive whole number, or left blank.",
    );
  }
  const claim: AtomicClaim = {
    id: draft.id,
    parameter_id: draft.parameter_id.trim() || null,
    label: draft.label.trim(),
    kind: draft.kind,
    stage: draft.stage.trim() || "general",
    operator: draft.operator,
    value:
      isNumeric && !["range", "one_of"].includes(draft.operator)
        ? numeric(draft.value)
        : null,
    minimum: draft.operator === "range" ? numeric(draft.minimum) : null,
    maximum: draft.operator === "range" ? numeric(draft.maximum) : null,
    tolerance: draft.operator === "=" ? numeric(draft.tolerance) : null,
    values:
      draft.operator === "one_of"
        ? draft.values
            .split(/[\n,]+/)
            .filter((value) => value.trim())
            .map((value) => numeric(value)!)
        : [],
    unit: draft.unit.trim() || null,
    text_value: !isNumeric ? draft.text_value.trim() || null : null,
    raw_text: wording,
    source_context: draft.source_context.trim() || null,
    configuration: draft.configuration.trim() || null,
    sequence: draft.sequence ? Number(draft.sequence) : null,
    source_pages: draft.original?.source_pages ?? [],
    availability: draft.availability,
    normalization_state:
      draft.parameter_id.trim() && !draft.parameter_id.startsWith("custom_")
        ? "reviewed"
        : "unresolved",
  };
  if (
    isNumeric &&
    claim.availability !== "supported" &&
    claim.value === null &&
    claim.minimum === null &&
    claim.maximum === null &&
    !claim.values.length
  ) {
    claim.operator = "required";
    claim.text_value =
      claim.availability === "unknown" ? "not yet assessed" : "not available";
  }
  if (
    claim.operator === "range" &&
    claim.minimum === null &&
    claim.maximum === null
  ) {
    throw new Error(`Add at least one range bound for ${claim.label}.`);
  }
  if (claim.operator === "one_of" && !claim.values.length) {
    throw new Error(`Add at least one allowed value for ${claim.label}.`);
  }
  if (
    claim.minimum !== null &&
    claim.maximum !== null &&
    Number(claim.minimum) > Number(claim.maximum)
  ) {
    throw new Error(`Minimum must not exceed maximum for ${claim.label}.`);
  }
  if (!isNumeric && !claim.text_value && claim.availability === "supported") {
    if (draft.operator === "required") claim.text_value = "available";
    else throw new Error(`Add the declared capability for ${claim.label}.`);
  }
  if (
    !["text", "required", "range", "one_of"].includes(claim.operator) &&
    claim.value === null
  ) {
    throw new Error(`Add the declared value for ${claim.label}.`);
  }
  // An untouched draft must not silently promote parsed/unresolved data to reviewed.
  if (draft.original) {
    const originalDraft = claimDraft(draft.original);
    if (
      Object.keys(draft).every(
        (key) =>
          key === "original" ||
          draft[key as keyof DraftClaim] ===
            originalDraft[key as keyof DraftClaim],
      )
    ) {
      return draft.original;
    }
  }
  return claim;
}

function declarationWording(draft: DraftClaim): string {
  if (
    draft.availability !== "supported" &&
    ![
      draft.value,
      draft.minimum,
      draft.maximum,
      draft.values,
      draft.text_value,
    ].some((entry) => entry.trim())
  ) {
    return `${draft.label.trim()}: ${draft.availability === "unknown" ? "not yet assessed" : "not available"}.`;
  }
  const value =
    draft.operator === "range"
      ? `${draft.minimum.trim() || "…"}–${draft.maximum.trim() || "…"}`
      : draft.operator === "one_of"
        ? draft.values.trim()
        : ["text", "required"].includes(draft.operator)
          ? draft.text_value.trim() || draft.availability
          : `${draft.operator === "=" ? "" : `${draft.operator} `}${draft.value.trim()}${draft.tolerance.trim() ? ` ± ${draft.tolerance.trim()}` : ""}`;
  return `${draft.label.trim()}: ${value}${draft.unit.trim() ? ` ${draft.unit.trim()}` : ""}${draft.source_context.trim() ? ` (${draft.source_context.trim()})` : ""}.`;
}
