import { Box, Stack, TextField, Typography } from "@mui/material";
import type { AtomicClaim } from "../types";
import type { DraftClaim } from "./labClaimDrafts";
import {
  resourceLabels,
  shortcutResources,
  shortcutResourceType,
  updateResourceDraft,
  type CapabilityResource,
} from "./labResourceClaims";

export default function CapabilityResources({
  claims,
  onChange,
}: {
  claims: DraftClaim[];
  onChange: (next: DraftClaim[]) => void;
}) {
  const resources = shortcutResources(claims);
  return (
    <Box
      component="section"
      aria-label="Resources"
      sx={{
        p: 2.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 2,
      }}
    >
      <Typography variant="subtitle1" fontWeight={650}>
        Resources{" "}
        <Typography component="span" variant="body2" color="text.secondary">
          (optional)
        </Typography>
      </Typography>
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ mt: 0.5, mb: 2 }}
      >
        Available for this test. Equipment categories are enough; no vendor or
        model needed.
      </Typography>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        {(["equipment", "supplies"] as CapabilityResource[]).map((type) => {
          const multiple =
            claims.filter((claim) => shortcutResourceType(claim) === type)
              .length > 1;
          return (
            <TextField
              key={type}
              label={resourceLabels[type]}
              value={resources[type]?.text_value ?? ""}
              onChange={(event) =>
                onChange(updateResourceDraft(claims, type, event.target.value))
              }
              multiline
              minRows={2}
              maxRows={5}
              fullWidth
              disabled={multiple}
              inputProps={{ maxLength: 2000 }}
              placeholder={
                type === "equipment"
                  ? "e.g. Environmental chamber; pressure-water-jet tester"
                  : "e.g. Adhesion test tape; deionized water"
              }
              helperText={
                multiple
                  ? "Multiple entries are recorded in Operating conditions below."
                  : type === "equipment"
                    ? "Separate categories with semicolons or new lines."
                    : "Testing supplies, not the customer's specimen material. Clear to remove."
              }
            />
          );
        })}
      </Stack>
    </Box>
  );
}

export function CapabilityResourcesSummary({
  claims,
}: {
  claims: AtomicClaim[];
}) {
  const resources = shortcutResources(claims);
  const entries = (["equipment", "supplies"] as CapabilityResource[]).filter(
    (type) => resources[type],
  );
  if (!entries.length) return null;
  return (
    <Box
      component="section"
      aria-label="Resources"
      sx={{
        p: 2.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 2,
      }}
    >
      <Typography variant="subtitle1" fontWeight={650} sx={{ mb: 1.5 }}>
        Resources
      </Typography>
      <Stack direction={{ xs: "column", sm: "row" }} gap={2.5}>
        {entries.map((type) => (
          <Box key={type} sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
              {resourceLabels[type]}
            </Typography>
            <Typography
              variant="body2"
              sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
            >
              {resources[type]!.text_value}
            </Typography>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}
