import { Box, Typography } from "@mui/material";

export interface SummaryFact {
  label: string;
  value: string;
  context?: string;
}

/** A short projection of saved facts. Full records stay available separately. */
export default function FactSummary({
  facts,
  emptyLabel = "Not recorded",
  limit = 2,
}: {
  facts: SummaryFact[];
  emptyLabel?: string;
  limit?: number;
}) {
  if (!facts.length)
    return (
      <Typography variant="body2" color="text.secondary">
        {emptyLabel}
      </Typography>
    );
  return (
    <Box sx={{ minWidth: 0 }}>
      {facts.slice(0, limit).map((fact, index) => (
        <Typography
          key={`${fact.label}-${index}`}
          variant="body2"
          sx={{
            overflowWrap: "anywhere",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            "& + &": { mt: 0.5 },
          }}
        >
          {fact.label && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {fact.label}:{" "}
            </Box>
          )}
          {fact.value}
          {fact.context && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {" "}
              ({fact.context})
            </Box>
          )}
        </Typography>
      ))}
      {facts.length > limit && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ mt: 0.5, display: "block" }}
        >
          +{facts.length - limit} more in details
        </Typography>
      )}
    </Box>
  );
}
