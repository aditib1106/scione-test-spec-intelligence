import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Stack,
  Typography,
} from "@mui/material";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined";

export default function SpecificationReviewSummary({
  documentNotes = [],
  coverageIssues = [],
}: {
  documentNotes?: string[];
  coverageIssues?: string[];
}) {
  const notes = [...new Set(documentNotes)];
  const checks = [...new Set(coverageIssues)];
  if (!notes.length && !checks.length) return null;

  return (
    <Accordion
      disableGutters
      elevation={0}
      sx={{
        border: "1px solid #e2e7f0",
        borderRadius: "12px !important",
        bgcolor: "#fff",
        "&:before": { display: "none" },
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreRoundedIcon />}
        sx={{ minHeight: 52, "& .MuiAccordionSummary-content": { my: 1 } }}
      >
        <Stack direction="row" gap={1.25} alignItems="center" flexWrap="wrap">
          <FactCheckOutlinedIcon sx={{ color: "#9c6417", fontSize: 21 }} />
          <Typography variant="body2" fontWeight={700}>
            Review summary
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {[
              notes.length
                ? `${notes.length} document ${notes.length === 1 ? "note" : "notes"}`
                : "",
              checks.length
                ? `${checks.length} source ${checks.length === 1 ? "check" : "checks"}`
                : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Stack>
      </AccordionSummary>
      <AccordionDetails sx={{ pt: 0, px: 2.5, pb: 2 }}>
        <Stack gap={2}>
          {[
            {
              title: "Document notes",
              guidance:
                "Check the document information and notes against the PDF.",
              issues: notes,
            },
            {
              title: "Source checks",
              guidance:
                "Check these references or shared instructions. Correct an existing test or add a missing test if needed.",
              issues: checks,
            },
          ].map(({ title, guidance, issues }) =>
            issues.length ? (
              <Box key={title}>
                <Typography variant="body2" fontWeight={700}>
                  {title}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  {guidance}
                </Typography>
                <Box component="ul" sx={{ pl: 2.5, mb: 0, mt: 1 }}>
                  {issues.map((issue) => (
                    <Typography
                      component="li"
                      variant="body2"
                      key={issue}
                      sx={{ mb: 0.75 }}
                    >
                      {issue}
                    </Typography>
                  ))}
                </Box>
              </Box>
            ) : null,
          )}
        </Stack>
      </AccordionDetails>
    </Accordion>
  );
}
