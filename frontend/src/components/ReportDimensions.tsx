import { Fragment, useState } from "react";
import {
  Box,
  ButtonBase,
  Chip,
  IconButton,
  Popover,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  Tooltip,
  TableRow,
  Typography,
} from "@mui/material";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import ContrastRoundedIcon from "@mui/icons-material/ContrastRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";
import HelpOutlineRoundedIcon from "@mui/icons-material/HelpOutlineRounded";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import type {
  ComparisonCheck,
  ComparisonRequirement,
} from "../comparisonTypes";

export type ReportDimension = "method" | "condition" | "criterion";
type ResultState = "match" | "partial" | "gap" | "unknown" | "note" | "empty";

const resultStyles = {
  match: { label: "Match", color: "#24745a", background: "#eaf5ee" },
  partial: { label: "Partial match", color: "#a56817", background: "#fff2d7" },
  gap: { label: "Gap", color: "#b34552", background: "#fdecef" },
  unknown: {
    label: "Needs information",
    color: "#315c9f",
    background: "#edf3fd",
  },
  note: { label: "Note", color: "#64748b", background: "#f0f3f7" },
  empty: { label: "Not specified", color: "#64748b", background: "#f0f3f7" },
};
const groups = [
  { key: "condition", label: "Condition", accessibleLabel: "Conditions" },
  {
    key: "criterion",
    label: "Criteria",
    accessibleLabel: "Criteria / reporting",
  },
] as const;

function belongs(check: ComparisonCheck): ReportDimension {
  if (
    check.kind === "method" ||
    ["method", "test_method"].includes(check.parameter_id ?? "")
  )
    return "method";
  if (/criter|report|measurement/.test(check.kind)) return "criterion";
  // Source questions and workflow notes must remain available for review.
  return "condition";
}

function groupState(checks: ComparisonCheck[]): ResultState {
  const hasGap = checks.some((check) => check.status === "gap");
  const hasMatch = checks.some((check) => check.status === "supported");
  if (hasGap) return hasMatch ? "partial" : "gap";
  if (checks.some((check) => check.status === "unknown")) return "unknown";
  if (hasMatch) return "match";
  return checks.length ? "note" : "empty";
}

function checkState(check: ComparisonCheck): ResultState {
  if (check.status === "supported") return "match";
  if (check.status === "gap") return "gap";
  if (check.status === "unknown") return "unknown";
  return "note";
}

function explanationText(reason: string) {
  if (
    reason ===
    "The stage, subject, source context, sequence or configuration is not equivalent."
  )
    return "Check whether this lab record applies to the requested conditions and test stage.";
  return reason.split(" Wording correspondence uses")[0];
}

function ResultBadge({ state }: { state: ResultState }) {
  const style = resultStyles[state];
  const icon =
    state === "match" ? (
      <CheckRoundedIcon />
    ) : state === "gap" ? (
      <CloseRoundedIcon />
    ) : state === "partial" ? (
      <ContrastRoundedIcon />
    ) : state === "unknown" ? (
      <HelpOutlineRoundedIcon />
    ) : undefined;
  return (
    <Chip
      size="small"
      icon={icon}
      label={style.label}
      sx={{
        height: "auto",
        minHeight: 28,
        maxWidth: "100%",
        borderRadius: "6px",
        bgcolor: style.background,
        color: style.color,
        fontSize: 12,
        fontWeight: 650,
        "& .MuiChip-label": { whiteSpace: "normal", py: 0.5, px: 1 },
        "& .MuiChip-icon": { color: "inherit", fontSize: 17, ml: 0.75 },
      }}
    />
  );
}

function contextLabel(check: ComparisonCheck, siblings: ComparisonCheck[]) {
  const phase =
    !check.stage || ["general", "measurement"].includes(check.stage)
      ? ""
      : check.stage.replaceAll("_", " ");
  const claim = check.requirement_claim;
  const step = claim?.sequence ? `Step ${claim.sequence}` : "";
  const temperatures =
    claim?.sequence && check.parameter_id !== "temperature"
      ? [
          ...new Set(
            siblings
              .filter(
                (other) =>
                  other.parameter_id === "temperature" &&
                  other.stage === check.stage &&
                  other.requirement_claim?.sequence === claim.sequence &&
                  (other.requirement_claim?.configuration ?? null) ===
                    (claim.configuration ?? null),
              )
              .map((other) => other.requested),
          ),
        ]
      : [];
  const sourceContext = claim?.source_context?.trim();
  const fold = (value: string) =>
    value.toLocaleLowerCase().replaceAll("_", " ").replace(/\s+/g, " ").trim();
  const redundantContext =
    sourceContext &&
    (fold(sourceContext).replace(/^measured /, "") === fold(check.label) ||
      fold(sourceContext) === `during ${fold(phase)}` ||
      fold(sourceContext) === fold(phase));
  return [
    ...new Set(
      [
        phase,
        step,
        temperatures.length === 1 ? temperatures[0] : "",
        claim?.configuration,
        redundantContext ? "" : sourceContext,
      ].filter(Boolean),
    ),
  ].join(" · ");
}

export default function ReportDimensions({
  requirement,
  expanded,
  onToggle,
}: {
  requirement: ComparisonRequirement;
  expanded: Partial<Record<ReportDimension, boolean>>;
  onToggle: (group: ReportDimension, open: boolean) => void;
}) {
  const [explanation, setExplanation] = useState<{
    anchor: HTMLElement;
    checks: ComparisonCheck[];
  } | null>(null);
  const methodChecks = requirement.comparison.checks.filter(
    (check) => belongs(check) === "method",
  );
  const requestedMethods =
    requirement.methods.join(" · ") ||
    [...new Set(methodChecks.map((check) => check.requested))].join(" · ");
  const declaredMethods = [
    ...new Set(methodChecks.map((check) => check.declared).filter(Boolean)),
  ].join(" · ");
  const methodState = methodChecks.length
    ? groupState(methodChecks)
    : requestedMethods
      ? "unknown"
      : "empty";

  const closeExplanation = () => {
    const anchor = explanation?.anchor;
    setExplanation(null);
    requestAnimationFrame(() => {
      if (anchor?.isConnected) anchor.focus({ preventScroll: true });
    });
  };

  function reasonButton(checks: ComparisonCheck[]) {
    if (!checks.some((check) => check.reason)) return null;
    return (
      <IconButton
        size="small"
        aria-label={`Reason for ${checks.length === 1 ? checks[0].label : "method comparison"}`}
        title="View explanation"
        onClick={(event) =>
          setExplanation({ anchor: event.currentTarget, checks })
        }
        sx={{ color: "#7a8898", p: 0.5, flexShrink: 0 }}
      >
        <InfoOutlinedIcon sx={{ fontSize: 18 }} />
      </IconButton>
    );
  }

  function parameterRows(checks: ComparisonCheck[]) {
    return checks.map((check, index) => {
      const context = contextLabel(check, requirement.comparison.checks);
      const candidates = check.candidate_lab_entries ?? [];
      return (
        <TableRow
          key={`${check.parameter_id}-${index}`}
          sx={{
            display: { xs: "grid", sm: "table-row" },
            gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
            gap: { xs: 1.5, sm: 0 },
            p: { xs: 2, sm: 0 },
            bgcolor: check.status === "gap" ? "#fff8f3" : "#fbfcfe",
            "& > td": { borderBottom: "1px solid #e8edf2" },
          }}
        >
          <TableCell
            sx={{
              gridColumn: { xs: "1 / -1", sm: "auto" },
              pl: { sm: "32px !important" },
            }}
          >
            <Typography fontSize={13} fontWeight={600} lineHeight={1.55}>
              {check.label}
            </Typography>
            {context && (
              <Typography
                fontSize={11.5}
                color="text.secondary"
                sx={{ mt: 0.5 }}
              >
                {context}
              </Typography>
            )}
          </TableCell>
          <TableCell>
            <Typography
              fontSize={11}
              color="text.secondary"
              sx={{ display: { xs: "block", sm: "none" }, mb: 0.5 }}
            >
              Customer specification
            </Typography>
            <Typography fontSize={13} lineHeight={1.6}>
              {check.requested}
            </Typography>
          </TableCell>
          <TableCell>
            <Typography
              fontSize={11}
              color="text.secondary"
              sx={{ display: { xs: "block", sm: "none" }, mb: 0.5 }}
            >
              Lab capability
            </Typography>
            {check.declared ? (
              <Typography fontSize={13} lineHeight={1.6}>
                {check.declared}
              </Typography>
            ) : candidates.length ? (
              <Stack gap={0.75}>
                {candidates.map((entry) => (
                  <Box key={entry.claim_id}>
                    <Typography fontSize={13} lineHeight={1.6}>
                      {entry.value}
                    </Typography>
                    {entry.context && (
                      <Typography variant="caption" color="text.secondary">
                        {entry.context}
                      </Typography>
                    )}
                  </Box>
                ))}
                <Tooltip title="Your lab has recorded this value, but its application to this requirement has not been confirmed. Open the result explanation for details.">
                  <Typography
                    component="span"
                    variant="caption"
                    tabIndex={0}
                    sx={{
                      color: "#946018",
                      width: "fit-content",
                      textDecoration: "underline dotted",
                      textUnderlineOffset: 3,
                      cursor: "help",
                    }}
                  >
                    Lab information to review
                  </Typography>
                </Tooltip>
              </Stack>
            ) : (
              <Typography fontSize={13} color="text.secondary">
                Not recorded
              </Typography>
            )}
          </TableCell>
          <TableCell sx={{ gridColumn: { xs: "1 / -1", sm: "auto" } }}>
            <Stack
              direction="row"
              gap={0.4}
              alignItems="center"
              sx={{ flexWrap: "nowrap" }}
            >
              <ResultBadge state={checkState(check)} />
              {reasonButton([check])}
            </Stack>
          </TableCell>
        </TableRow>
      );
    });
  }

  return (
    <>
      <TableContainer sx={{ overflowX: "auto" }}>
        <Table
          aria-label="Test capability comparison"
          sx={{
            display: { xs: "block", sm: "table" },
            width: "100%",
            tableLayout: "fixed",
            "& th, & td": {
              px: { xs: 0, sm: 2 },
              py: { xs: 0.25, sm: 1.75 },
              verticalAlign: "top",
              borderColor: "#e3e9ee",
              overflowWrap: "anywhere",
            },
            "& th": {
              bgcolor: "#f6f8fb",
              color: "#637487",
              fontSize: 12,
              fontWeight: 600,
              py: 1.5,
            },
            "& td": { fontSize: 14, color: "#263747" },
          }}
        >
          <colgroup>
            <col style={{ width: "22%" }} />
            <col style={{ width: "28%" }} />
            <col style={{ width: "28%" }} />
            <col style={{ width: "22%" }} />
          </colgroup>
          <TableHead sx={{ display: { xs: "none", sm: "table-header-group" } }}>
            <TableRow>
              <TableCell>Comparison</TableCell>
              <TableCell>Customer specification</TableCell>
              <TableCell>Lab capability</TableCell>
              <TableCell>Result</TableCell>
            </TableRow>
          </TableHead>
          <TableBody sx={{ display: { xs: "block", sm: "table-row-group" } }}>
            <TableRow
              sx={{
                display: { xs: "grid", sm: "table-row" },
                gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
                gap: { xs: 1.5, sm: 0 },
                p: { xs: 2, sm: 0 },
              }}
            >
              <TableCell
                sx={{
                  gridColumn: { xs: "1 / -1", sm: "auto" },
                  fontWeight: 650,
                }}
              >
                Method
              </TableCell>
              <TableCell>
                <Typography
                  fontSize={11}
                  color="text.secondary"
                  sx={{ display: { xs: "block", sm: "none" }, mb: 0.5 }}
                >
                  Customer specification
                </Typography>
                <Box component="span">
                  {requestedMethods || "Not specified"}
                </Box>
              </TableCell>
              <TableCell>
                <Typography
                  fontSize={11}
                  color="text.secondary"
                  sx={{ display: { xs: "block", sm: "none" }, mb: 0.5 }}
                >
                  Lab capability
                </Typography>
                <Box component="span">
                  {declaredMethods || (
                    <Box component="span" sx={{ color: "text.secondary" }}>
                      {methodState === "match"
                        ? "Matching method listed"
                        : "Not recorded"}
                    </Box>
                  )}
                </Box>
              </TableCell>
              <TableCell sx={{ gridColumn: { xs: "1 / -1", sm: "auto" } }}>
                <Stack
                  direction="row"
                  gap={0.4}
                  alignItems="center"
                  sx={{ flexWrap: "nowrap" }}
                >
                  <ResultBadge state={methodState} />
                  {reasonButton(methodChecks)}
                </Stack>
              </TableCell>
            </TableRow>
          </TableBody>
          {groups.map(({ key, label, accessibleLabel }) => {
            const checks = requirement.comparison.checks.filter(
              (check) => belongs(check) === key,
            );
            const open = Boolean(expanded[key]);
            const detailsId = `report-${requirement.requirement_id}-${key}-details`;
            return (
              <Fragment key={key}>
                <TableBody
                  sx={{ display: { xs: "block", sm: "table-row-group" } }}
                >
                  <TableRow
                    sx={{
                      display: { xs: "flex", sm: "table-row" },
                      bgcolor: open ? "#f6f9fc" : "#fff",
                    }}
                  >
                    <TableCell colSpan={3} sx={{ py: "0 !important", flex: 1 }}>
                      <ButtonBase
                        aria-label={accessibleLabel}
                        aria-expanded={open}
                        aria-controls={detailsId}
                        onClick={() => onToggle(key, !open)}
                        sx={{
                          width: "100%",
                          minHeight: 58,
                          gap: 1,
                          textAlign: "left",
                          justifyContent: "flex-start",
                          borderRadius: 1,
                          "&.Mui-focusVisible": {
                            outline: "2px solid #2456ca",
                            outlineOffset: -3,
                          },
                        }}
                      >
                        <ChevronRightRoundedIcon
                          sx={{
                            fontSize: 20,
                            color: "#60758b",
                            transform: open ? "rotate(90deg)" : undefined,
                            transition: "transform 180ms ease",
                          }}
                        />
                        <Typography fontSize={14} fontWeight={650}>
                          {label}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ ml: 0.5 }}
                        >
                          {checks.length || "No"}{" "}
                          {checks.length === 1 ? "requirement" : "requirements"}
                        </Typography>
                      </ButtonBase>
                    </TableCell>
                    <TableCell
                      sx={{
                        display: { xs: "flex", sm: "table-cell" },
                        alignItems: "center",
                        maxWidth: { xs: "52%", sm: "none" },
                        py: "15px !important",
                      }}
                    >
                      <ResultBadge state={groupState(checks)} />
                    </TableCell>
                  </TableRow>
                </TableBody>
                {open && (
                  <TableBody
                    id={detailsId}
                    aria-label={`${requirement.test_name} ${accessibleLabel.toLowerCase()} comparison`}
                    sx={{ display: { xs: "block", sm: "table-row-group" } }}
                  >
                    {checks.length ? (
                      parameterRows(checks)
                    ) : (
                      <TableRow>
                        <TableCell
                          colSpan={4}
                          sx={{ color: "text.secondary" }}
                        >{`No ${label.toLowerCase()} recorded for this test.`}</TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                )}
              </Fragment>
            );
          })}
        </Table>
      </TableContainer>
      <Popover
        open={Boolean(explanation)}
        anchorEl={explanation?.anchor}
        onClose={closeExplanation}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        disableScrollLock
        disableRestoreFocus
        slotProps={{
          paper: {
            role: "dialog",
            "aria-label": `Reason for ${explanation?.checks.length === 1 ? explanation.checks[0].label : "method comparison"}`,
            sx: { maxWidth: 360, p: 2 },
          },
        }}
      >
        <Stack spacing={1.5}>
          {explanation?.checks.map((check, index) => (
            <Box key={index}>
              <Typography fontSize={14} fontWeight={650} sx={{ mb: 0.5 }}>
                {check.label}
              </Typography>
              <Typography fontSize={14} lineHeight={1.6}>
                {explanationText(check.reason)}
              </Typography>
              {check.source_pages.length > 0 && (
                <Typography
                  fontSize={12}
                  color="text.secondary"
                  sx={{ mt: 0.75 }}
                >
                  Source: {check.source_pages.length === 1 ? "page" : "pages"}{" "}
                  {check.source_pages.join(", ")}
                </Typography>
              )}
            </Box>
          ))}
        </Stack>
      </Popover>
    </>
  );
}
