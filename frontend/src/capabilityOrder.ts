import type { Capability, LabProfile, Workspace } from "./types";

/** Creation metadata belongs to the UI, never the scientific lab declaration. */
export function capabilityCreationTimes(
  lab: LabProfile,
  metadata: Workspace["capability_creation"],
): ReadonlyMap<string, number> {
  const times = new Map<string, number>();
  if (
    !metadata ||
    metadata.lab_id !== lab.id ||
    metadata.lab_version !== lab.version ||
    !metadata.created_at ||
    typeof metadata.created_at !== "object" ||
    Array.isArray(metadata.created_at)
  )
    return times;
  for (const capability of lab.capabilities) {
    const value = Object.hasOwn(metadata.created_at, capability.id)
      ? metadata.created_at[capability.id]
      : null;
    // Stored timestamps have an explicit zone; a bare date is not creation time.
    if (typeof value !== "string" || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value))
      continue;
    const timestamp = Date.parse(value);
    if (Number.isFinite(timestamp)) times.set(capability.id, timestamp);
  }
  return times;
}

export function capabilitiesNewestFirst(
  capabilities: readonly Capability[],
  created: ReadonlyMap<string, number>,
): Capability[] {
  return capabilities
    .map((capability, index) => ({ capability, index }))
    .sort((a, b) => {
      const first = created.get(a.capability.id),
        second = created.get(b.capability.id);
      if (first !== undefined && second !== undefined)
        return second - first || a.index - b.index;
      if (first !== undefined) return -1;
      if (second !== undefined) return 1;
      // Imported records without dates retain their original relative order.
      return a.index - b.index;
    })
    .map((entry) => entry.capability);
}
