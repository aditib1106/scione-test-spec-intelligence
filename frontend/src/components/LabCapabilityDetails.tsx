import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Chip,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import type { AtomicClaim, OperatingLimit } from "../types";

export const capabilityDetailSx = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "12px !important",
  overflow: "hidden",
  "&:before": { display: "none" },
  "& .MuiAccordionSummary-root": { px: 2.25, minHeight: 78 },
  "& .MuiAccordionSummary-content": { my: 1.5 },
} as const;

export function labClaimValue(claim: AtomicClaim) {
  if (claim.availability === "unknown") return "Not yet assessed";
  if (claim.availability === "unsupported") return "Not available";
  let text = claim.text_value ?? claim.raw_text;
  if (claim.operator === "range")
    text = `${claim.minimum ?? "…"}–${claim.maximum ?? "…"}`;
  else if (claim.operator === "one_of") text = claim.values.join(" / ");
  else if (claim.value !== null)
    text = `${claim.operator === "=" ? "" : `${claim.operator} `}${claim.value}${claim.tolerance !== null ? ` ± ${claim.tolerance}` : ""}`;
  return `${text}${claim.unit ? ` ${claim.unit}` : ""}`;
}

const labelFor = (value: string) =>
  value
    .replaceAll("_", " ")
    .replace(/^\w/, (character) => character.toUpperCase());

function limitValue(limit: OperatingLimit) {
  if (limit.supported_values.length) return limit.supported_values.join("; ");
  const value = limit.values.length
    ? limit.values.join(" / ")
    : `${limit.minimum ?? "…"}–${limit.maximum ?? "…"}`;
  return `${value}${limit.unit ? ` ${limit.unit}` : ""}`;
}

export default function LabCapabilityDetails({
  title,
  description,
  claims,
  wording,
  limits = [],
  onExpand,
}: {
  title: string;
  description: string;
  claims: AtomicClaim[];
  wording?: string | null;
  limits?: OperatingLimit[];
  onExpand: () => void;
}) {
  const count = claims.length + limits.length;
  return (
    <Accordion
      disableGutters
      elevation={0}
      sx={capabilityDetailSx}
      onChange={(_, expanded) => {
        if (expanded) onExpand();
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
        <Box sx={{ flex: 1, minWidth: 0, pr: 1.5 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="subtitle1" fontWeight={650}>
              {title}
            </Typography>
            <Chip
              size="small"
              label={count || (wording ? "Notes" : "Not recorded")}
            />
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3 }}>
            {description}
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails
        sx={{ p: 0, borderTop: "1px solid", borderColor: "divider" }}
      >
        {count > 0 && (
          <TableContainer>
            <Table
              size="small"
              aria-label={`${title} saved fields`}
              sx={{
                minWidth: 560,
                tableLayout: "fixed",
                "& th": { bgcolor: "grey.50", py: 1.25 },
                "& td": {
                  py: 1.75,
                  verticalAlign: "top",
                  overflowWrap: "anywhere",
                },
                "& tbody tr:last-child td": { borderBottom: 0 },
              }}
            >
              <TableHead>
                <TableRow>
                  <TableCell sx={{ width: "29%" }}>Parameter</TableCell>
                  <TableCell sx={{ width: "45%" }}>Your lab supports</TableCell>
                  <TableCell sx={{ width: "26%" }}>Applies to</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {claims.map((claim) => (
                  <TableRow key={claim.id}>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>
                        {claim.label}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      {claim.availability === "supported" ? (
                        <Typography
                          variant="body2"
                          sx={{ whiteSpace: "pre-wrap" }}
                        >
                          {labClaimValue(claim)}
                        </Typography>
                      ) : (
                        <Chip
                          size="small"
                          label={labClaimValue(claim)}
                          color={
                            claim.availability === "unsupported"
                              ? "error"
                              : "default"
                          }
                          variant="outlined"
                        />
                      )}
                      {claim.availability === "supported" &&
                        claim.normalization_state === "unresolved" && (
                          <Typography variant="caption" color="warning.main">
                            Needs clarification
                          </Typography>
                        )}
                    </TableCell>
                    <TableCell>
                      <Stack spacing={0.35}>
                        <Typography variant="body2">
                          {claim.stage === "general"
                            ? "All stages"
                            : labelFor(claim.stage)}
                        </Typography>
                        {claim.source_context && (
                          <Typography variant="caption" color="text.secondary">
                            {claim.source_context}
                          </Typography>
                        )}
                        {claim.configuration && (
                          <Typography variant="caption" color="text.secondary">
                            Setup: {claim.configuration}
                          </Typography>
                        )}
                        {claim.sequence !== null && (
                          <Typography variant="caption" color="text.secondary">
                            Step {claim.sequence}
                          </Typography>
                        )}
                      </Stack>
                    </TableCell>
                  </TableRow>
                ))}
                {limits.map((limit, index) => (
                  <TableRow key={`${limit.parameter}-${index}`}>
                    <TableCell>{labelFor(limit.parameter)}</TableCell>
                    <TableCell>{limitValue(limit)}</TableCell>
                    <TableCell>General limit</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        {wording && (
          <Box
            sx={{
              p: 2.25,
              ...(count
                ? { borderTop: "1px solid", borderColor: "divider" }
                : {}),
            }}
          >
            {count > 0 ? (
              <Box component="details">
                <Typography
                  component="summary"
                  variant="body2"
                  fontWeight={600}
                  color="text.secondary"
                  sx={{ cursor: "pointer" }}
                >
                  Additional description
                </Typography>
                <Typography
                  variant="body2"
                  sx={{
                    mt: 1,
                    whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere",
                  }}
                >
                  {wording}
                </Typography>
              </Box>
            ) : (
              <Typography
                variant="body2"
                sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
              >
                {wording}
              </Typography>
            )}
          </Box>
        )}
        {count === 0 && !wording && (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2.25 }}>
            No information recorded. Choose Edit capability to add it.
          </Typography>
        )}
      </AccordionDetails>
    </Accordion>
  );
}
