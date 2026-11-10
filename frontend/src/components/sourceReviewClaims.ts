import type { AtomicClaim, RequirementValue } from "../types";

export type SourceReviewClaim = AtomicClaim & {
  display_value?: string;
  source_atom_ids?: string[];
};

export function isSamplePlanClaim(claim: SourceReviewClaim): boolean {
  return (
    ["scope", "instruction"].includes(claim.kind) ||
    ["submitted_lot_count", "sample_allocation"].includes(
      claim.parameter_id ?? "",
    )
  );
}

export function isReviewParameter(claim: SourceReviewClaim): boolean {
  return Boolean(
    claim.parameter_id ||
    claim.source_atom_ids?.length ||
    claim.value != null ||
    claim.minimum != null ||
    claim.maximum != null ||
    claim.values?.length,
  );
}

export function claimValue(claim: SourceReviewClaim): string {
  if (claim.display_value) return claim.display_value;
  const unit = claim.unit ? ` ${claim.unit}` : "";
  if (["text", "required"].includes(claim.operator))
    return claim.text_value || claim.raw_text;
  if (claim.operator === "range")
    return `${claim.minimum ?? "…"}–${claim.maximum ?? "…"}${unit}`;
  if (claim.operator === "one_of") return `${claim.values.join(", ")}${unit}`;
  const operator =
    ({ "<=": "≤", ">=": "≥", "=": "" } as Record<string, string>)[
      claim.operator
    ] ?? claim.operator;
  return `${operator}${operator ? " " : ""}${claim.value ?? "…"}${claim.tolerance ? ` ± ${claim.tolerance}` : ""}${unit}`;
}

export function claimContext(claim: SourceReviewClaim): string {
  const stage =
    claim.stage && claim.stage !== "general"
      ? claim.stage.replaceAll("_", " ")
      : "";
  return [
    stage,
    claim.sequence ? `step ${claim.sequence}` : "",
    claim.configuration,
    claim.source_context,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function originalValue(value: RequirementValue): string {
  return `${value.raw_value}${value.unit && !value.raw_value.includes(value.unit) ? ` ${value.unit}` : ""}`;
}
