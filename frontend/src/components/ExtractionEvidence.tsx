import { useEffect, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import FindInPageOutlinedIcon from "@mui/icons-material/FindInPageOutlined";
import SourcePageReference from "./SourcePageReference";
import type { SourceField } from "./ExtractionReviewTable";
import type { Requirement } from "../types";
import {
  claimContext,
  claimValue,
  isSamplePlanClaim,
  originalValue,
  type SourceReviewClaim,
} from "./sourceReviewClaims";

export default function ExtractionEvidence({
  requirement,
  claims,
  notices,
  note,
  onNote,
  onSource,
}: {
  requirement: Requirement;
  claims?: SourceReviewClaim[];
  notices: string[];
  note: string;
  onNote: (value: string) => void;
  onSource: (field: SourceField | null, page: number, claimId?: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<"evidence" | "instructions">("evidence");
  useEffect(() => {
    setExpanded(false);
    setTab("evidence");
  }, [requirement.id]);
  const planning = (claims ?? requirement.atomic_claims ?? []).filter(
    isSamplePlanClaim,
  );
  const fold = (text: string) =>
    text.toLocaleLowerCase().replace(/\s+/g, " ").trim();
  const excerpts = requirement.evidence.filter(
    (item, index, all) =>
      all.findIndex(
        (other) =>
          other.page === item.page && fold(other.quote) === fold(item.quote),
      ) === index,
  );
  const additionalWording = [
    ...requirement.conditions.map((item) => ({
      label: "Condition text",
      text: originalValue(item),
    })),
    ...requirement.acceptance_criteria.map((item) => ({
      label: "Acceptance criteria text",
      text: originalValue(item),
    })),
  ].filter(
    (item, index, all) =>
      item.text.trim() &&
      !excerpts.some((excerpt) =>
        fold(excerpt.quote).includes(fold(item.text)),
      ) &&
      all.findIndex((other) => fold(other.text) === fold(item.text)) === index,
  );
  const instructions = [
    ...new Set([
      ...requirement.applicability,
      ...requirement.special_instructions,
    ]),
  ].filter(
    (text) =>
      text.trim() &&
      !planning.some((claim) => fold(claim.raw_text) === fold(text)),
  );
  const hasInstructions = planning.length > 0 || instructions.length > 0;

  return (
    <Accordion
      expanded={expanded}
      onChange={(_, open) => setExpanded(open)}
      disableGutters
      elevation={0}
      sx={{
        border: "1px solid #dce6f3",
        borderRadius: "8px !important",
        overflow: "hidden",
        "&:before": { display: "none" },
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreRoundedIcon fontSize="small" />}
        sx={{
          minHeight: 36,
          px: 1.25,
          bgcolor: "#f7faff",
          "& .MuiAccordionSummary-content": { my: 0.75 },
        }}
      >
        <Stack direction="row" alignItems="center" gap={0.75}>
          <FindInPageOutlinedIcon sx={{ fontSize: 18, color: "#4166a3" }} />
          <Typography variant="body2" fontWeight={650} color="#315b96">
            View extraction evidence
          </Typography>
        </Stack>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1.5, pt: 0.5, pb: 1.5 }}>
        {hasInstructions && (
          <Tabs
            value={tab}
            onChange={(_, value) => setTab(value)}
            variant="fullWidth"
            aria-label="Extraction evidence sections"
            sx={{
              minHeight: 36,
              mb: 1.5,
              borderBottom: "1px solid #e2e8f0",
              "& .MuiTab-root": { minHeight: 36, fontSize: 12, px: 1 },
            }}
          >
            <Tab value="evidence" label="PDF evidence" />
            <Tab value="instructions" label="Sample & instructions" />
          </Tabs>
        )}
        {tab === "evidence" ? (
          <Stack gap={1.5}>
            <Typography variant="caption" color="text.secondary">
              Saved passages used to extract this test. Select a page to locate
              the text in the PDF.
            </Typography>
            {excerpts.map((evidence, index) => (
              <Box
                key={`${evidence.page}-${index}`}
                sx={{
                  pl: 1.25,
                  borderLeft: "3px solid #c8d8ef",
                }}
              >
                <SourcePageReference
                  page={evidence.page}
                  label={`Locate extraction evidence on page ${evidence.page}`}
                  onClick={() => onSource(null, evidence.page)}
                />
                <Typography
                  component="blockquote"
                  variant="body2"
                  sx={{
                    m: 0,
                    mt: 0.5,
                    whiteSpace: "pre-wrap",
                    lineHeight: 1.65,
                  }}
                >
                  {evidence.quote}
                </Typography>
              </Box>
            ))}
            {!excerpts.length && (
              <Typography variant="body2" color="text.secondary">
                No PDF quotation was saved for this test.
              </Typography>
            )}
            {additionalWording.map((item, index) => (
              <Box key={index}>
                <Typography
                  variant="caption"
                  fontWeight={650}
                  color="text.secondary"
                >
                  {item.label}
                </Typography>
                <Typography
                  variant="body2"
                  sx={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}
                >
                  {item.text}
                </Typography>
              </Box>
            ))}
            {notices.map((notice, index) => (
              <Box
                key={index}
                sx={{ p: 1.25, bgcolor: "#fff8e9", borderRadius: 1 }}
              >
                <Typography variant="caption" fontWeight={650}>
                  Method note
                </Typography>
                <Typography variant="body2" sx={{ mt: 0.5, lineHeight: 1.6 }}>
                  {notice.replace(/\breq_[A-Za-z0-9_-]+\b[: ]*/g, "")}
                </Typography>
              </Box>
            ))}
            <TextField
              multiline
              minRows={2}
              maxRows={5}
              size="small"
              fullWidth
              label="Review note (optional)"
              value={note}
              onChange={(event) => onNote(event.target.value)}
              inputProps={{ maxLength: 4000 }}
            />
          </Stack>
        ) : (
          <Stack gap={1.5}>
            <Typography variant="caption" color="text.secondary">
              Sample preparation, quantities and other instructions saved from
              this specification.
            </Typography>
            {planning.map((claim) => (
              <Box
                key={claim.id}
                sx={{
                  display: "grid",
                  gridTemplateColumns: "minmax(100px, 0.8fr) minmax(0, 1.2fr)",
                  gap: 1.25,
                  pb: 1.25,
                  borderBottom: "1px solid #e5ebf3",
                }}
              >
                <Typography
                  variant="body2"
                  fontWeight={600}
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {claim.label}
                </Typography>
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
                    {claimValue(claim)}
                  </Typography>
                  {claimContext(claim) && (
                    <Typography variant="caption" color="text.secondary">
                      {claimContext(claim)}
                    </Typography>
                  )}
                  <Stack direction="row" flexWrap="wrap" gap={0.25}>
                    {claim.source_pages.map((page) => (
                      <SourcePageReference
                        key={page}
                        page={page}
                        label={`View sample plan source on page ${page} for ${claim.label}`}
                        onClick={() => onSource(null, page, claim.id)}
                      />
                    ))}
                  </Stack>
                </Box>
              </Box>
            ))}
            {instructions.map((text, index) => (
              <Typography key={index} variant="body2" sx={{ lineHeight: 1.65 }}>
                {text}
              </Typography>
            ))}
          </Stack>
        )}
      </AccordionDetails>
    </Accordion>
  );
}
