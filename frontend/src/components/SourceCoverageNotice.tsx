import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Box, Button, Collapse, Stack, Typography } from "@mui/material";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";

/** A document-level extraction check, separate from lab capability decisions. */
export default function SourceCoverageNotice({
  issues,
  sourceReviewUrl,
}: {
  issues: string[];
  sourceReviewUrl?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!issues.length) return null;
  return (
    <Alert
      severity="warning"
      sx={{
        py: 0.25,
        flexShrink: 0,
        bgcolor: "#fffbf2",
        border: "1px solid #efd9ae",
        borderRadius: 2,
        "& .MuiAlert-icon": { fontSize: 20, pt: 1 },
        "& .MuiAlert-message": { width: "100%", minWidth: 0, py: 0.5 },
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Box sx={{ flex: "1 1 200px" }}>
          <Typography variant="body2" fontWeight={650}>
            Document notes
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {issues.length} {issues.length === 1 ? "note" : "notes"} to check
          </Typography>
        </Box>
        {sourceReviewUrl && (
          <Button component={RouterLink} to={sourceReviewUrl} size="small">
            Review specification
          </Button>
        )}
        <Button
          size="small"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          endIcon={
            <ExpandMoreRoundedIcon
              sx={{ transform: expanded ? "rotate(180deg)" : undefined }}
            />
          }
          sx={{ whiteSpace: "nowrap", color: "#835817" }}
        >
          Review notes
        </Button>
      </Stack>
      <Collapse in={expanded}>
        <Box sx={{ borderTop: "1px solid #efdfc4", mt: 0.75, pt: 1 }}>
          <Typography variant="body2" sx={{ mb: 1 }}>
            Check the PDF for omitted or incomplete requirements. Correct an
            existing test, or add one if needed. This check concerns extraction,
            not whether the lab can perform the test.
          </Typography>
          <Stack component="ul" gap={0.75} sx={{ m: 0, pl: 2 }}>
            {issues.map((issue, index) => (
              <Typography
                component="li"
                key={index}
                variant="body2"
                sx={{ overflowWrap: "anywhere" }}
              >
                {issue}
              </Typography>
            ))}
          </Stack>
        </Box>
      </Collapse>
    </Alert>
  );
}
