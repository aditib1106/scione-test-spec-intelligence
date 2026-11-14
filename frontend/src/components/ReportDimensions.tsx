import { Fragment, useRef, useState } from "react";
import {
  Box,
  Divider,
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
  ReasonSource,
} from "../comparisonTypes";
import type { PopoverActions } from "@mui/material";

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
    label: "Acceptance criteria",
    accessibleLabel: "Acceptance criteria / reporting",
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
  const validationMessages: Record<string, string> = {
    "Semantic scope cannot equate explicit distinct phases.":
      "The customer requirement and this lab entry apply to different test stages.",
    "Semantic scope cannot merge distinct configurations.":
      "The customer requirement and this lab entry use different test setups.",
    "Semantic scope cannot change an explicit step sequence.":
      "The customer requirement and this lab entry refer to different test steps.",
    "Semantic scope cannot change medium, layer or location.":
      "The customer requirement and this lab entry refer to different materials, layers or measurement locations.",
    "Semantic scope cannot supply a missing setup identifier.":
      "The lab entry does not identify the setup required by the customer.",
    "Linked declarations use different parameter policies.":
      "The selected lab entries describe different measurements and cannot be combined for this requirement.",
    "The source fact has not been normalized reliably; semantic correspondence does not repair or replace source facts.":
      "Clarify the extracted requirement before comparing it with the lab's capability.",
  };
  if (validationMessages[reason]) return validationMessages[reason];
  if (
    reason ===
    "The stage, subject, source context, sequence or configuration is not equivalent."
  )
    return "Check whether this lab record applies to the requested conditions and test stage.";
  return reason.split(" Wording correspondence uses")[0];
}

function reasonAuthor(
  check: ComparisonCheck,
  source?: ReasonSource,
): "AI" | "System" | "AI-assisted" | null {
  if (source === "system") return "System";
  if (source === "ai") return "AI";
  if (source === "ai_assisted") return "AI-assisted";
  if (
    (check.comparison_metadata ?? []).some(
      (entry) =>
        entry.semantic_inference &&
        entry.reason?.trim() === check.reason.trim(),
    )
  )
    return "AI";
  if (
    [
      "deterministic_exact_fields",
      "deterministic_literal_text",
      "source_optional_route_policy",
    ].includes(check.comparison_basis ?? "") ||
    check.comparison_metadata?.some((entry) => entry.numeric_evaluation) ||
    new Set([
      "The requested setting or range is covered by this lab declaration in the same context.",
      "The request exceeds this lab declaration's stated range in the same context.",
      "The lab has not declared a complete selectable or reportable range.",
      "The lab has no declaration for this parameter in the requested stage.",
      "The requested test/method is present in the selected lab declaration.",
      "The requested test/method is not listed by this lab.",
      "The requested test/method still needs an identity decision.",
    ]).has(explanationText(check.reason))
  )
    return "System";
  return null;
}

function AuthorBadge({ label }: { label: "AI" | "System" | "AI-assisted" }) {
  return (
    <Chip
      size="small"
      label={label}
      sx={{
        height: 21,
        fontSize: 10.5,
        fontWeight: 650,
        bgcolor: label === "System" ? "#eef1f5" : "#eaf0fc",
        color: label === "System" ? "#556477" : "#315b96",
        "& .MuiChip-label": { px: 0.8 },
      }}
    />
  );
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
  reasonSources,
  expanded,
  onToggle,
}: {
  requirement: ComparisonRequirement;
  reasonSources?: Record<string, ReasonSource>;
  expanded: Partial<Record<ReportDimension, boolean>>;
  onToggle: (group: ReportDimension, open: boolean) => void;
}) {
  const [explanation, setExplanation] = useState<{
    anchor: HTMLElement;
    checks: ComparisonCheck[];
  } | null>(null);
  const explanationActions = useRef<PopoverActions>(null);
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
        title="Why this result?"
        onClick={(event) =>
          setExplanation({ anchor: event.currentTarget, checks })
        }
        sx={{
          color: "#456b93",
          p: 0.6,
          flexShrink: 0,
          "&:hover": { bgcolor: "#edf3fb", color: "primary.main" },
        }}
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
        action={explanationActions}
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
            sx: {
              width: 480,
              maxWidth: "calc(100vw - 32px)",
              maxHeight: "min(680px, calc(100dvh - 48px))",
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
              borderRadius: 2,
              border: "1px solid #dfe7ef",
            },
          },
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ px: 2.5, py: 1.5, bgcolor: "#f6f8fb", flexShrink: 0 }}
        >
          <Typography fontSize={15} fontWeight={650}>
            Why this result?
          </Typography>
          <IconButton
            size="small"
            aria-label="Close explanation"
            onClick={closeExplanation}
          >
            <CloseRoundedIcon fontSize="small" />
          </IconButton>
        </Stack>
        <Stack
          role="region"
          aria-label="Result explanation details"
          tabIndex={0}
          divider={<Divider />}
          sx={{
            minHeight: 0,
            overflowY: "auto",
            overscrollBehavior: "contain",
          }}
        >
          {explanation?.checks.map((check, index) => {
            const author = reasonAuthor(
              check,
              check.id ? reasonSources?.[check.id] : undefined,
            );
            const aiAssisted = [
              "semantic_alignment",
              "literal_descriptive_correspondence",
            ].includes(check.comparison_basis ?? "");
            const notes = [
              ...new Set(
                (check.comparison_metadata ?? [])
                  .filter((entry) => entry.semantic_inference && entry.reason)
                  .map((entry) => entry.reason!.trim())
                  .filter((reason) => reason !== check.reason),
              ),
            ];
            const quote = check.requirement_claim?.raw_text?.trim();
            return (
              <Stack key={index} spacing={1.75} sx={{ p: 2.5 }}>
                <Stack
                  direction="row"
                  alignItems="flex-start"
                  justifyContent="space-between"
                  gap={1.5}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    flexWrap="wrap"
                    gap={0.75}
                  >
                    <Typography fontSize={15} fontWeight={650}>
                      {check.label}
                    </Typography>
                    {aiAssisted && <AuthorBadge label="AI-assisted" />}
                  </Stack>
                  <ResultBadge state={checkState(check)} />
                </Stack>
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                    border: "1px solid #e3e9ee",
                    borderRadius: 1.5,
                    overflow: "hidden",
                  }}
                >
                  {[
                    {
                      label: "Customer requirement",
                      text: check.requested,
                    },
                    {
                      label: "Recorded lab capability",
                      text:
                        check.declared ||
                        "No matching lab declaration recorded",
                    },
                  ].map(({ label, text }, cellIndex) => (
                    <Box
                      key={label}
                      sx={{
                        p: 1.5,
                        minWidth: 0,
                        bgcolor: cellIndex ? "#f6f8fb" : "#fff",
                        borderLeft: cellIndex ? "1px solid #e3e9ee" : undefined,
                      }}
                    >
                      <Typography
                        fontSize={11.5}
                        fontWeight={600}
                        color="text.secondary"
                        sx={{ mb: 0.75 }}
                      >
                        {label}
                      </Typography>
                      <Typography
                        fontSize={13}
                        lineHeight={1.6}
                        sx={{ overflowWrap: "anywhere" }}
                      >
                        {text}
                      </Typography>
                    </Box>
                  ))}
                </Box>
                <Box>
                  <Stack
                    direction="row"
                    alignItems="center"
                    gap={0.75}
                    sx={{ mb: 0.5 }}
                  >
                    {author && <AuthorBadge label={author} />}
                    <Typography fontSize={12} fontWeight={650}>
                      Assessment reason
                    </Typography>
                  </Stack>
                  <Typography fontSize={13} lineHeight={1.7}>
                    {explanationText(check.reason)}
                  </Typography>
                </Box>
                {check.candidate_lab_entries?.length ? (
                  <Box>
                    <Typography fontSize={12} fontWeight={650} sx={{ mb: 0.5 }}>
                      Related lab information
                    </Typography>
                    {check.candidate_lab_entries.map((entry) => (
                      <Typography
                        key={entry.claim_id}
                        fontSize={13}
                        lineHeight={1.7}
                        sx={{ mb: 0.5 }}
                      >
                        {entry.label}: {entry.value}
                        {entry.context ? ` · ${entry.context}` : ""}
                      </Typography>
                    ))}
                    <Typography
                      fontSize={12}
                      lineHeight={1.6}
                      color="text.secondary"
                    >
                      This entry was not used to confirm support.
                    </Typography>
                  </Box>
                ) : null}
                {notes.map((note) => (
                  <Box key={note}>
                    <Stack
                      direction="row"
                      alignItems="center"
                      gap={0.75}
                      sx={{ mb: 0.5 }}
                    >
                      <AuthorBadge label="AI" />
                      <Typography fontSize={12} fontWeight={650}>
                        Matching note
                      </Typography>
                    </Stack>
                    <Typography fontSize={13} lineHeight={1.7}>
                      {note}
                    </Typography>
                  </Box>
                ))}
                {(quote || check.source_pages.length > 0) && (
                  <Box
                    component="details"
                    sx={{ fontSize: 12 }}
                    onToggle={() =>
                      requestAnimationFrame(() =>
                        explanationActions.current?.updatePosition(),
                      )
                    }
                  >
                    <Box
                      component="summary"
                      sx={{
                        cursor: "pointer",
                        color: "primary.main",
                        fontWeight: 600,
                        py: 0.25,
                      }}
                    >
                      Specification evidence
                      {check.source_pages.length > 0 &&
                        ` · ${check.source_pages.length === 1 ? "Page" : "Pages"} ${check.source_pages.join(", ")}`}
                    </Box>
                    {quote && (
                      <Typography
                        component="blockquote"
                        fontSize={12.5}
                        lineHeight={1.75}
                        sx={{
                          m: 0,
                          mt: 1,
                          pl: 1.5,
                          borderLeft: "3px solid #d6e3f2",
                          color: "text.secondary",
                        }}
                      >
                        {quote}
                      </Typography>
                    )}
                  </Box>
                )}
              </Stack>
            );
          })}
        </Stack>
      </Popover>
    </>
  );
}
