/** A report is generated only by an explicit click, never by opening a URL. */
export interface ReportGenerationIntent {
  generateReportFor: string;
  generationIntentId: string;
}

const consumed = new Set<string>();
const storageKey = "scione.report.consumed-generation-intents";

export function readReportGenerationIntent(
  value: unknown,
  documentId: string,
): ReportGenerationIntent | null {
  if (!value || typeof value !== "object") return null;
  const state = value as Partial<ReportGenerationIntent>;
  return state.generateReportFor === documentId &&
    typeof state.generationIntentId === "string" &&
    state.generationIntentId.length >= 8 &&
    state.generationIntentId.length <= 120
    ? (state as ReportGenerationIntent)
    : null;
}

export function claimReportGenerationIntent(intentId: string): boolean {
  let saved: string[] = [];
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
    if (Array.isArray(value))
      saved = value.filter((item) => typeof item === "string");
  } catch {
    // Memory still prevents duplicate effects when browser storage is unavailable.
  }
  if (consumed.has(intentId) || saved.includes(intentId)) return false;
  consumed.add(intentId);
  try {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify([...saved.slice(-99), intentId]),
    );
  } catch {
    // Optional storage does not block an explicitly requested generation.
  }
  return true;
}
