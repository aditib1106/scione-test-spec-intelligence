import { useEffect, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import SourcePageReference from "./SourcePageReference";
import type { AtomicClaim, Requirement, RequirementValue } from "../types";
import {
  claimContext,
  claimValue,
  originalValue,
  isReviewParameter,
  isSamplePlanClaim,
  type SourceReviewClaim,
} from "./sourceReviewClaims";

export type SourceField = "name" | "method" | "condition" | "criterion";

export default function ExtractionReviewTable({
  requirement,
  claims,
  displayName,
  sourceField,
  sourceClaimId,
  onSource,
  onEditClaim,
}: {
  requirement: Requirement;
  claims?: SourceReviewClaim[];
  displayName?: string;
  sourceField: SourceField | null;
  sourceClaimId?: string | null;
  onSource: (field: SourceField, page: number, claimId?: string) => void;
  onEditClaim?: (claimId: string) => void;
}) {
  const atoms = claims ?? requirement.atomic_claims ?? [];
  const conditions = atoms.filter(
    (claim) => claim.kind === "condition" && !isSamplePlanClaim(claim),
  );
  const criteria = atoms.filter((claim) =>
    ["criterion", "reporting"].includes(claim.kind),
  );
  const [group, setGroup] = useState<"condition" | "criterion">("condition");
  useEffect(() => {
    setGroup(
      conditions.length || requirement.conditions.length
        ? "condition"
        : "criterion",
    );
  }, [requirement.id]);
  const pages = (claim?: AtomicClaim) =>
    [
      ...new Set(
        claim?.source_pages.length
          ? claim.source_pages
          : requirement.evidence.map((evidence) => evidence.page),
      ),
    ].sort((a, b) => a - b);
  const sourceLinks = (field: SourceField, claim?: AtomicClaim) => (
    <Stack direction="row" flexWrap="wrap" gap={0.25}>
      {pages(claim).map((page) => (
        <SourcePageReference
          key={page}
          page={page}
          label={`View ${field} source on page ${page}${claim ? ` for ${claim.label}` : ""}`}
          onClick={() => onSource(field, page, claim?.id)}
        />
      ))}
    </Stack>
  );
  const identitySource = (field: "name" | "method") => {
    const exact = requirement.evidence.find((evidence) =>
      field === "method"
        ? requirement.requested_methods.some((method) =>
            evidence.quote.includes(method.code),
          )
        : evidence.quote.includes(requirement.original_test_name),
    );
    const page = exact?.page ?? requirement.evidence[0]?.page;
    return page ? (
      <Box sx={{ mt: 0.4 }}>
        <SourcePageReference
          page={page}
          label={`View ${field} source on page ${page}`}
          onClick={() => onSource(field, page)}
        />
      </Box>
    ) : null;
  };
  const active = group === "condition" ? conditions : criteria;
  const concrete = active.filter(isReviewParameter);
  // These closed connective fragments add no requirement; original wording and
  // saved machine claims remain intact and available separately.
  const other = active.filter(
    (claim) =>
      !isReviewParameter(claim) &&
      !/^(?:we want|we require|we ask|please|and|or|between|the latter|before|after)[.!:]?$/i.test(
        (claim.text_value || claim.raw_text).trim(),
      ),
  );
  const rawValues =
    group === "condition"
      ? requirement.conditions
      : requirement.acceptance_criteria;
  const originalWording = (items: RequirementValue[]) => (
    <Stack gap={1.25}>
      {items.map((item, index) => (
        <Typography
          key={index}
          variant="body2"
          sx={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}
        >
          {originalValue(item)}
        </Typography>
      ))}
    </Stack>
  );
  return (
    <Stack gap={1.5}>
      <TableContainer sx={{ border: "1px solid #dce4ef", borderRadius: 2 }}>
        <Table
          aria-label="Test details"
          size="small"
          sx={{
            tableLayout: "fixed",
            "& td, & th": {
              verticalAlign: "top",
              p: 1,
              borderColor: "#e3e9f1",
            },
          }}
        >
          <TableBody>
            <TableRow
              sx={{ bgcolor: sourceField === "name" ? "#f1f6ff" : "#fff" }}
            >
              <TableCell
                component="th"
                scope="row"
                sx={{ width: 106, bgcolor: "#f5f8fc", fontWeight: 650 }}
              >
                Test name
                {identitySource("name")}
              </TableCell>
              <TableCell>
                <Typography
                  component="h2"
                  variant="body2"
                  fontWeight={700}
                  title={requirement.original_test_name}
                  sx={{
                    overflowWrap: "anywhere",
                    lineHeight: 1.5,
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {displayName || requirement.original_test_name}
                </Typography>
              </TableCell>
            </TableRow>
            <TableRow
              sx={{ bgcolor: sourceField === "method" ? "#f1f6ff" : "#fff" }}
            >
              <TableCell
                component="th"
                scope="row"
                sx={{ bgcolor: "#f5f8fc", fontWeight: 650 }}
              >
                Method
                {identitySource("method")}
              </TableCell>
              <TableCell>
                <Stack direction="row" flexWrap="wrap" gap={0.75}>
                  {requirement.requested_methods.map((method, index) => (
                    <Tooltip
                      key={index}
                      title={
                        method.role
                          ? `Source use: ${method.role}`
                          : "Method stated in the specification"
                      }
                    >
                      <Chip
                        size="small"
                        variant="outlined"
                        label={`${method.code}${method.revision ? ` (${method.revision})` : ""}`}
                        sx={{
                          fontWeight: 600,
                          bgcolor: "#f8faff",
                          borderColor: "#cfdcf5",
                        }}
                      />
                    </Tooltip>
                  ))}
                  {!requirement.requested_methods.length && (
                    <Typography variant="body2" color="text.secondary">
                      No method number stated
                    </Typography>
                  )}
                </Stack>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </TableContainer>
      <Box>
        <Tabs
          value={group}
          onChange={(_, value) => setGroup(value)}
          variant="fullWidth"
          aria-label="Requirement groups"
          sx={{
            minHeight: 40,
            borderBottom: "1px solid #dce4ef",
            "& .MuiTab-root": {
              minHeight: 40,
              py: 1,
              fontSize: 13,
              fontWeight: 700,
            },
          }}
        >
          <Tab
            value="condition"
            label={`Conditions (${conditions.filter(isReviewParameter).length})`}
          />
          <Tab
            value="criterion"
            label={`Criteria (${criteria.filter(isReviewParameter).length})`}
          />
        </Tabs>
        {concrete.length > 0 && (
          <TableContainer>
            <Table
              aria-label={
                group === "condition"
                  ? "Condition parameters"
                  : "Criteria parameters"
              }
              size="small"
              sx={{
                tableLayout: "fixed",
                "& th, & td": {
                  verticalAlign: "top",
                  borderColor: "#e1e8f1",
                  px: 1.25,
                  py: 1.25,
                },
                "& tbody tr:nth-of-type(even)": { bgcolor: "#f8fafd" },
              }}
            >
              <TableHead>
                <TableRow sx={{ bgcolor: "#f0f4fa" }}>
                  <TableCell sx={{ width: "40%", fontWeight: 650 }}>
                    Parameter
                  </TableCell>
                  <TableCell sx={{ fontWeight: 650 }}>
                    Customer requirement
                  </TableCell>
                  <TableCell sx={{ width: 38, px: "2px !important" }} />
                </TableRow>
              </TableHead>
              <TableBody>
                {concrete.map((claim) => (
                  <TableRow
                    key={claim.id}
                    selected={sourceClaimId === claim.id}
                    sx={{ "&.Mui-selected": { bgcolor: "#edf4ff" } }}
                  >
                    <TableCell>
                      <Typography
                        variant="body2"
                        fontWeight={600}
                        sx={{ overflowWrap: "anywhere" }}
                      >
                        {claim.label}
                      </Typography>
                      {sourceLinks(group, claim)}
                    </TableCell>
                    <TableCell>
                      <Typography
                        variant="body2"
                        sx={{
                          fontWeight: 600,
                          lineHeight: 1.55,
                          overflowWrap: "anywhere",
                        }}
                      >
                        {claimValue(claim)}
                      </Typography>
                      {claimContext(claim) && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{
                            display: "block",
                            mt: 0.4,
                            overflowWrap: "anywhere",
                          }}
                        >
                          {claimContext(claim)}
                        </Typography>
                      )}
                      {claim.normalization_state === "unresolved" && (
                        <Chip
                          size="small"
                          label="Check interpretation"
                          sx={{
                            mt: 0.75,
                            height: 22,
                            fontSize: 11,
                            bgcolor: "#fff2db",
                            color: "#92590c",
                          }}
                        />
                      )}
                    </TableCell>
                    <TableCell sx={{ px: "2px !important" }}>
                      {onEditClaim && (
                        <Tooltip title={`Edit ${claim.label}`}>
                          <Button
                            size="small"
                            aria-label={`Edit parameter ${claim.label}`}
                            onClick={() => onEditClaim(claim.id)}
                            sx={{ minWidth: 30, p: 0.5 }}
                          >
                            <EditOutlinedIcon sx={{ fontSize: 17 }} />
                          </Button>
                        </Tooltip>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        {!concrete.length && !rawValues.length && !other.length && (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No {group === "condition" ? "conditions" : "criteria"} stated.
          </Typography>
        )}
        {other.length > 0 && (
          <Accordion
            disableGutters
            elevation={0}
            sx={{
              border: "1px solid #e1e7ef",
              borderRadius: "8px !important",
              mt: 1.25,
              overflow: "hidden",
              "&:before": { display: "none" },
            }}
          >
            <AccordionSummary
              expandIcon={<ExpandMoreRoundedIcon />}
              sx={{ minHeight: 40, bgcolor: "#f8fafc" }}
            >
              <Typography variant="body2" fontWeight={600}>
                Source notes ({other.length})
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ p: 0 }}>
              <Stack divider={<Box sx={{ borderTop: "1px solid #e5ebf2" }} />}>
                {other.map((claim) => {
                  // An unresolved parser residual is an internal comparison
                  // token, not readable source wording. Keep its original text
                  // in this presentation; the saved claim remains unchanged.
                  const wording =
                    claim.normalization_state === "unresolved"
                      ? claim.raw_text
                      : claim.text_value || claim.raw_text;
                  const alternative =
                    /if (?:an? )?ASTM route is approved/i.test(wording);
                  return (
                    <Box
                      key={claim.id}
                      sx={{
                        display: "grid",
                        gridTemplateColumns: "minmax(0, 1fr) auto",
                        gap: 1,
                        p: 1.25,
                      }}
                    >
                      <Box sx={{ minWidth: 0 }}>
                        {alternative ? (
                          <Box component="details">
                            <Typography
                              component="summary"
                              variant="body2"
                              fontWeight={600}
                              sx={{ cursor: "pointer" }}
                            >
                              Alternative method needs customer approval
                            </Typography>
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              sx={{
                                mt: 0.75,
                                lineHeight: 1.6,
                                overflowWrap: "anywhere",
                              }}
                            >
                              {wording}
                            </Typography>
                          </Box>
                        ) : (
                          <Typography
                            variant="body2"
                            sx={{
                              whiteSpace: "pre-wrap",
                              lineHeight: 1.6,
                              overflowWrap: "anywhere",
                            }}
                          >
                            {wording}
                          </Typography>
                        )}
                      </Box>
                      <Stack direction="row" alignItems="flex-start" gap={0.25}>
                        {sourceLinks(group, claim)}
                        {onEditClaim && (
                          <Tooltip title="Edit note">
                            <Button
                              size="small"
                              aria-label={`Edit source note ${claim.label}`}
                              onClick={() => onEditClaim(claim.id)}
                              sx={{ minWidth: 28, p: 0.4 }}
                            >
                              <EditOutlinedIcon sx={{ fontSize: 16 }} />
                            </Button>
                          </Tooltip>
                        )}
                      </Stack>
                    </Box>
                  );
                })}
              </Stack>
            </AccordionDetails>
          </Accordion>
        )}
        {rawValues.length > 0 && (
          <Accordion
            disableGutters
            elevation={0}
            defaultExpanded={!active.length}
            sx={{ mt: 1, "&:before": { display: "none" } }}
          >
            <AccordionSummary
              expandIcon={<ExpandMoreRoundedIcon />}
              sx={{ minHeight: 40, px: 1 }}
            >
              <Typography variant="caption" fontWeight={600}>
                Original specification wording
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ px: 1 }}>
              {!active.length && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mb: 1 }}
                >
                  Detailed parameters are not available for this saved
                  extraction.
                </Typography>
              )}
              {displayName &&
                displayName !== requirement.original_test_name && (
                  <Typography variant="body2" sx={{ mb: 1.25 }}>
                    <Box component="span" fontWeight={650}>
                      Original test label:{" "}
                    </Box>
                    {requirement.original_test_name}
                  </Typography>
                )}
              {originalWording(rawValues)}
              {sourceLinks(group)}
            </AccordionDetails>
          </Accordion>
        )}
      </Box>
    </Stack>
  );
}
