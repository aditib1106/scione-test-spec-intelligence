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
    color: "#966116",
    background: "#fff5e3",
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
        fontSize: 13,
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
  return [
    phase,
    step,
    temperatures.length === 1 ? temperatures[0] : "",
    claim?.configuration,
    claim?.source_context,
  ]
    .filter(Boolean)
    .join(" · ");
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

  function parameterTable(checks: ComparisonCheck[], accessibleLabel: string) {
    return (
      <TableContainer
        sx={{
          border: "1px solid #dde6e0",
          borderRadius: "8px",
          bgcolor: "#fff",
        }}
      >
        <Table
          size="small"
          aria-label={`${requirement.test_name} ${accessibleLabel.toLowerCase()} comparison`}
          sx={{
            minWidth: { xs: 0, sm: 560 },
            display: { xs: "block", sm: "table" },
            tableLayout: "fixed",
            "& th": {
              bgcolor: "#f7f9f8",
              textTransform: "none",
              fontSize: 13,
              letterSpacing: 0,
              py: 1.75,
              px: 2,
            },
            "& td": {
              verticalAlign: "top",
              py: { xs: 0.5, sm: 2 },
              px: { xs: 0, sm: 2 },
              borderBottom: { xs: 0, sm: "1px solid #e3e9e5" },
            },
            "& tr:last-child td": { borderBottom: 0 },
          }}
        >
          <TableHead sx={{ display: { xs: "none", sm: "table-header-group" } }}>
            <TableRow>
              <TableCell sx={{ width: "25%" }}>Parameter</TableCell>
              <TableCell sx={{ width: "28%" }}>
                Customer specification
              </TableCell>
              <TableCell sx={{ width: "27%" }}>Lab capability</TableCell>
              <TableCell sx={{ width: "20%" }}>Result</TableCell>
            </TableRow>
          </TableHead>
          <TableBody sx={{ display: { xs: "block", sm: "table-row-group" } }}>
            {checks.map((check, index) => {
              const context = contextLabel(
                check,
                requirement.comparison.checks,
              );
              return (
                <TableRow
                  key={`${check.parameter_id}-${index}`}
                  sx={{
                    display: { xs: "grid", sm: "table-row" },
                    gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
                    columnGap: 2,
                    p: { xs: 2, sm: 0 },
                    borderBottom: { xs: "1px solid #e3e9e5", sm: 0 },
                    bgcolor: check.status === "gap" ? "#fffaf5" : "transparent",
                    "&:last-child": { borderBottom: 0 },
                  }}
                >
                  <TableCell sx={{ gridColumn: { xs: "1 / -1", sm: "auto" } }}>
                    <Typography
                      fontSize={14}
                      fontWeight={600}
                      lineHeight={1.55}
                    >
                      {check.label}
                    </Typography>
                    {context && (
                      <Typography
                        fontSize={12}
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
                    <Typography fontSize={14} lineHeight={1.6}>
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
                    <Typography
                      component="div"
                      fontSize={14}
                      lineHeight={1.6}
                      color={check.declared ? "text.primary" : "text.secondary"}
                    >
                      {check.declared ||
                        (check.candidate_lab_entries?.length ? (
                          <Stack gap={0.5}>
                            <Typography
                              component="span"
                              variant="caption"
                              sx={{ color: "#946018", fontWeight: 600 }}
                            >
                              Possible lab entry
                            </Typography>
                            {check.candidate_lab_entries.map((entry) => (
                              <Box component="span" key={entry.claim_id}>
                                {entry.value}
                                <Typography
                                  component="span"
                                  display="block"
                                  variant="caption"
                                  color="text.secondary"
                                >
                                  {entry.label}
                                  {entry.stage !== "general"
                                    ? ` · ${entry.stage.replaceAll("_", " ")}`
                                    : ""}
                                  {entry.context && (
                                    <Box component="span" display="block">
                                      {entry.context}
                                    </Box>
                                  )}
                                </Typography>
                              </Box>
                            ))}
                          </Stack>
                        ) : (
                          "Not recorded"
                        ))}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ gridColumn: { xs: "1 / -1", sm: "auto" } }}>
                    <Stack
                      direction="row"
                      spacing={0.5}
                      alignItems="center"
                      sx={{ flexWrap: "wrap" }}
                    >
                      <ResultBadge state={checkState(check)} />
                      {reasonButton([check])}
                    </Stack>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    );
  }

  return (
    <>
      <TableContainer>
        <Table
          aria-label="Test capability comparison"
          sx={{
            display: { xs: "block", sm: "table" },
            tableLayout: "fixed",
            "& th, & td": {
              borderColor: "#e3e9ee",
              overflowWrap: "anywhere",
              px: { xs: 1.25, sm: 2.5 },
            },
            "& th": {
              color: "#788595",
              fontSize: { xs: 11, sm: 12 },
              fontWeight: 600,
              py: 2,
            },
            "& td": { fontSize: 15, color: "#263747" },
          }}
        >
          <TableHead sx={{ display: { xs: "none", sm: "table-header-group" } }}>
            <TableRow>
              <TableCell sx={{ width: "20%" }}>Comparison</TableCell>
              <TableCell sx={{ width: "30%" }}>
                Customer specification
              </TableCell>
              <TableCell sx={{ width: "30%" }}>Lab capability</TableCell>
              <TableCell align="right" sx={{ width: "20%" }}>
                Result
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody sx={{ display: { xs: "block", sm: "table-row-group" } }}>
            <TableRow
              sx={{
                display: { xs: "grid", sm: "table-row" },
                gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
                columnGap: 2,
                p: { xs: 2, sm: 0 },
                borderBottom: { xs: "1px solid #e3e9ee", sm: 0 },
                "& > td": {
                  px: { xs: 0, sm: 2.5 },
                  py: { xs: 0.75, sm: 3 },
                  borderBottom: { xs: 0, sm: "1px solid #e3e9ee" },
                },
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
                    <Box component="span" sx={{ color: "#788595" }}>
                      {methodState === "match"
                        ? "Matching method listed"
                        : "Not recorded"}
                    </Box>
                  )}
                </Box>
              </TableCell>
              <TableCell
                align="right"
                sx={{ gridColumn: { xs: "1 / -1", sm: "auto" } }}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                  sx={{
                    flexWrap: "wrap",
                    justifyContent: { xs: "flex-start", sm: "flex-end" },
                  }}
                >
                  <ResultBadge state={methodState} />
                  {reasonButton(methodChecks)}
                </Stack>
              </TableCell>
            </TableRow>
            {groups.map(({ key, label, accessibleLabel }) => {
              const checks = requirement.comparison.checks.filter(
                (check) => belongs(check) === key,
              );
              const open = Boolean(expanded[key]);
              const detailsId = `report-${requirement.requirement_id}-${key}-details`;
              return (
                <Fragment key={key}>
                  <TableRow
                    sx={{
                      display: { xs: "flex", sm: "table-row" },
                      borderBottom: { xs: "1px solid #e3e9ee", sm: 0 },
                    }}
                  >
                    <TableCell
                      colSpan={3}
                      sx={{
                        py: 0,
                        pr: "0 !important",
                        flex: 1,
                        minWidth: 0,
                        borderBottom: { xs: 0, sm: "1px solid #e3e9ee" },
                      }}
                    >
                      <ButtonBase
                        aria-label={accessibleLabel}
                        aria-expanded={open}
                        aria-controls={detailsId}
                        onClick={() => onToggle(key, !open)}
                        sx={{
                          width: "100%",
                          minHeight: { xs: 64, sm: 76 },
                          justifyContent: "flex-start",
                          gap: 1.25,
                          textAlign: "left",
                          borderRadius: 1,
                          "&.Mui-focusVisible": {
                            outline: "2px solid #2456ca",
                            outlineOffset: -3,
                          },
                        }}
                      >
                        <ChevronRightRoundedIcon
                          sx={{
                            fontSize: 21,
                            color: open ? "#2e6e59" : "#82948b",
                            transform: open ? "rotate(90deg)" : undefined,
                            transition: "transform 120ms",
                            border: "1px solid #dbe5df",
                            borderRadius: "4px",
                          }}
                        />
                        <Typography fontSize={15} fontWeight={650}>
                          {label}
                        </Typography>
                      </ButtonBase>
                    </TableCell>
                    <TableCell
                      align="right"
                      sx={{
                        py: 0,
                        display: { xs: "flex", sm: "table-cell" },
                        alignItems: "center",
                        justifyContent: "flex-end",
                        maxWidth: { xs: "55%", sm: "none" },
                        borderBottom: { xs: 0, sm: "1px solid #e3e9ee" },
                      }}
                    >
                      <ResultBadge state={groupState(checks)} />
                    </TableCell>
                  </TableRow>
                  {open && (
                    <TableRow
                      sx={{ display: { xs: "block", sm: "table-row" } }}
                    >
                      <TableCell
                        colSpan={4}
                        sx={{
                          display: { xs: "block", sm: "table-cell" },
                          pt: 0,
                          pb: 2.5,
                        }}
                      >
                        <Box id={detailsId}>
                          {checks.length ? (
                            parameterTable(checks, accessibleLabel)
                          ) : (
                            <Typography
                              fontSize={14}
                              color="text.secondary"
                              sx={{ pl: 4 }}
                            >
                              No {label.toLowerCase()} recorded for this test.
                            </Typography>
                          )}
                        </Box>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
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
                {check.reason.split(" Wording correspondence uses")[0]}
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
