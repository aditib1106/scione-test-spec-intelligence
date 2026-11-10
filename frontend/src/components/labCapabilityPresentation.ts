import type { AtomicClaim, Capability } from "../types";

// Presentation only: shared declarations remain stored on every service and
// remain available to matching. Never collapse a numeric limit or a restriction.
export function sharedDeclarationKey(claim: AtomicClaim): string | null {
  if (
    !["instruction", "reporting", "scope"].includes(claim.kind) ||
    !["text", "required"].includes(claim.operator) ||
    claim.stage !== "general" ||
    claim.availability !== "supported" ||
    !claim.text_value ||
    claim.sequence !== null ||
    claim.configuration
  )
    return null;
  return JSON.stringify([
    claim.parameter_id,
    claim.label,
    claim.kind,
    claim.text_value,
    claim.source_context ?? null,
    claim.normalization_state,
  ]);
}

export function sharedDeclarations(capabilities: Capability[]): AtomicClaim[] {
  if (capabilities.length < 2) return [];
  const counts = new Map<string, number>();
  for (const capability of capabilities) {
    const keys = new Set(
      (capability.atomic_claims ?? [])
        .map(sharedDeclarationKey)
        .filter((key): key is string => key !== null),
    );
    for (const key of keys) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return (capabilities[0].atomic_claims ?? []).filter((claim) => {
    const key = sharedDeclarationKey(claim);
    return key !== null && counts.get(key) === capabilities.length;
  });
}

export function serviceClaims(
  claims: AtomicClaim[],
  sharedKeys: Set<string>,
): AtomicClaim[] {
  return claims
    .filter((claim) => !sharedKeys.has(sharedDeclarationKey(claim) ?? ""))
    .sort((a, b) => {
      const rank = (claim: AtomicClaim) =>
        claim.availability !== "supported"
          ? 0
          : ["text", "required"].includes(claim.operator)
            ? 2
            : 1;
      return rank(a) - rank(b);
    });
}
