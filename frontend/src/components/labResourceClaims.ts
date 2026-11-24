import type { AtomicClaim } from "../types";
import { claimDraft, type DraftClaim } from "./labClaimDrafts";

export type CapabilityResource = "equipment" | "supplies";
export const resourceLabels: Record<CapabilityResource, string> = {
  equipment: "Equipment categories",
  supplies: "Consumables & materials",
};
const parameters: Record<CapabilityResource, string> = {
  equipment: "equipment",
  supplies: "custom_lab_supplies",
};
const absent = (value: unknown) => value == null || value === "";

/** Only entries created by this shortcut are owned here; legacy facts stay put. */
export function shortcutResourceType(
  claim: AtomicClaim | DraftClaim,
): CapabilityResource | null {
  const type = (Object.keys(parameters) as CapabilityResource[]).find((key) =>
    new RegExp(`^cap_resource_${key}_[a-f0-9]{32}$`).test(claim.id),
  );
  return type &&
    claim.parameter_id === parameters[type] &&
    claim.label === resourceLabels[type] &&
    claim.kind === "condition" &&
    claim.stage === "general" &&
    claim.operator === "text" &&
    claim.availability === "supported" &&
    absent(claim.value) &&
    absent(claim.minimum) &&
    absent(claim.maximum) &&
    absent(claim.tolerance) &&
    !claim.values.length &&
    !claim.unit &&
    !claim.configuration &&
    !claim.source_context &&
    absent(claim.sequence) &&
    ("source_pages" in claim
      ? !claim.source_pages.length
      : !claim.original?.source_pages.length)
    ? type
    : null;
}

export function shortcutResources<T extends AtomicClaim | DraftClaim>(
  claims: T[],
): Partial<Record<CapabilityResource, T>> {
  const result: Partial<Record<CapabilityResource, T>> = {};
  for (const type of Object.keys(parameters) as CapabilityResource[]) {
    const entries = claims.filter(
      (claim) => shortcutResourceType(claim) === type,
    );
    // Unexpected imported duplicates remain visible in the full field editor.
    if (entries.length === 1) result[type] = entries[0];
  }
  return result;
}

export function updateResourceDraft(
  claims: DraftClaim[],
  type: CapabilityResource,
  text: string,
): DraftClaim[] {
  const current = shortcutResources(claims)[type];
  if (!text.trim()) return claims.filter((claim) => claim !== current);
  if (current) {
    return claims.map((claim) =>
      claim === current ? { ...claim, text_value: text, raw_text: "" } : claim,
    );
  }
  return [
    ...claims,
    {
      ...claimDraft(),
      id: `cap_resource_${type}_${crypto.randomUUID().replaceAll("-", "")}`,
      parameter_id: parameters[type],
      label: resourceLabels[type],
      kind: "condition",
      stage: "general",
      operator: "text",
      text_value: text,
    },
  ];
}
