interface Reviewer {
  actor?: string;
  actor_label?: string | null;
}
interface CurrentUser {
  id: string;
  display_name: string;
  username: string;
}

/** Prefer the name captured when saved; never guess another staff member's name. */
export function reviewerLabel(
  record: Reviewer | undefined,
  current?: CurrentUser | null,
): string {
  if (typeof record?.actor_label === "string" && record.actor_label.trim())
    return record.actor_label.trim();
  if (record?.actor === "local_reviewer") return "Local reviewer";
  if (record?.actor && record.actor === current?.id)
    return current.display_name.trim() || current.username.trim();
  return record?.actor ? "Lab reviewer" : "Reviewer not recorded";
}
