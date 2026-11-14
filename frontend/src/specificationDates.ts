/** Customer-document version dates; upload timestamps are separate metadata. */
export function specificationVersionLabel(document: {
  specification_date?: string | null;
}): string {
  const value = document.specification_date;
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return "Date not provided";
  const parsed = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(parsed.valueOf())) return "Date not provided";
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function compareSpecificationDates(
  first: { specification_date?: string | null; created_at: string },
  second: { specification_date?: string | null; created_at: string },
): number {
  return (
    (second.specification_date ?? "").localeCompare(
      first.specification_date ?? "",
    ) || second.created_at.localeCompare(first.created_at)
  );
}
