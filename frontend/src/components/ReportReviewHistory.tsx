import { useEffect, useId, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import HistoryRoundedIcon from "@mui/icons-material/HistoryRounded";
import FileDownloadOutlinedIcon from "@mui/icons-material/FileDownloadOutlined";
import { request } from "../api";
import { useAuth } from "../auth";
import type { AssessmentReview, ComparisonData } from "../comparisonTypes";
import RelatedReviewReferences from "./RelatedReviewReferences";
import { reviewerLabel } from "../reviewerLabels";

interface Props {
  documentId: string;
  report: ComparisonData;
  requirementId?: string;
}

interface HistoryEntry extends AssessmentReview {
  carried_from_review_id?: string;
  carried_from_comparison_id?: string;
}

interface HistoryResponse {
  document_id: string;
  comparison_id: string;
  events: HistoryEntry[];
}

const decisionLabels: Record<AssessmentReview["decision"], string> = {
  keep: "Keep the current assessment",
  clarify_customer: "Ask the customer to clarify",
  outsource: "Plan to outsource this test",
  decline: "Do not offer this test",
};

function dateLabel(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
}

/** Remount the panel on context changes so no old lab/document history can flash. */
export default function ReportReviewHistory(props: Props) {
  return (
    <HistoryPanel
      key={JSON.stringify([
        props.documentId,
        props.report.comparison_id,
        props.requirementId ?? null,
      ])}
      {...props}
    />
  );
}

function HistoryPanel({ documentId, report, requirementId }: Props) {
  const { session } = useAuth();
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [events, setEvents] = useState<HistoryEntry[] | null>(null);
  const [loadedRevision, setLoadedRevision] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [historyTab, setHistoryTab] = useState<"current" | "related">(
    "current",
  );
  const revision = Object.values(report.assessment_reviews ?? {})
    .map(
      (review) =>
        `${review.requirement_id}:${review.version}:${review.review_id}`,
    )
    .sort()
    .join("|");
  const names = useMemo(
    () =>
      new Map(
        report.requirements.map((item) => [
          item.requirement_id,
          item.test_name,
        ]),
      ),
    [report.requirements],
  );

  useEffect(() => {
    if (!expanded || historyTab !== "current") return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    // A changed saved review must not temporarily display its previous history.
    setEvents(null);
    void request<HistoryResponse>(
      `/api/comparisons/${encodeURIComponent(documentId)}/reviews`,
      { signal: controller.signal },
    )
      .then((history) => {
        if (controller.signal.aborted) return;
        if (
          history.document_id !== documentId ||
          history.comparison_id !== report.comparison_id ||
          !Array.isArray(history.events) ||
          history.events.some(
            (event) => !event || event.comparison_id !== report.comparison_id,
          )
        )
          throw new Error(
            "The saved assessment changed. Refresh the report before viewing its review history.",
          );
        setEvents(
          [...history.events].sort(
            (a, b) =>
              (Date.parse(b.created_at) || 0) -
                (Date.parse(a.created_at) || 0) || b.version - a.version,
          ),
        );
        setLoadedRevision(revision);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Review history could not be loaded. Please try again.",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [
    documentId,
    report.comparison_id,
    expanded,
    revision,
    attempt,
    historyTab,
  ]);

  const visible =
    loadedRevision === revision
      ? events?.filter(
          (event) => !requirementId || event.requirement_id === requirementId,
        )
      : null;
  const pending =
    loading || (expanded && !error && loadedRevision !== revision);
  return (
    <>
      <Button
        size="small"
        color="inherit"
        aria-haspopup="dialog"
        aria-expanded={expanded}
        aria-controls={`${panelId}-dialog`}
        startIcon={<HistoryRoundedIcon fontSize="small" />}
        onClick={() => setExpanded(true)}
        sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
      >
        Review history
      </Button>
      <Dialog
        open={expanded}
        onClose={() => setExpanded(false)}
        aria-labelledby={`${panelId}-title`}
        aria-describedby={`${panelId}-subtitle`}
        fullWidth
        maxWidth={historyTab === "related" ? "md" : "sm"}
        PaperProps={{
          id: `${panelId}-dialog`,
          sx: { maxHeight: "min(80dvh, 720px)", borderRadius: 2.5 },
        }}
      >
        <DialogTitle
          id={`${panelId}-heading`}
          sx={{ p: { xs: 2, sm: 3 }, pr: 7, pb: 2 }}
        >
          <Stack direction="row" gap={1.5} alignItems="center">
            <Box
              sx={{
                width: 38,
                height: 38,
                borderRadius: 2,
                bgcolor: "#eef3fc",
                color: "#486b9e",
                display: "grid",
                placeItems: "center",
                flexShrink: 0,
              }}
            >
              <HistoryRoundedIcon sx={{ fontSize: 21 }} />
            </Box>
            <Box sx={{ minWidth: 0 }}>
              <Box
                component="span"
                id={`${panelId}-title`}
                sx={{ fontSize: 20, fontWeight: 700 }}
              >
                Review history
              </Box>
              <Typography
                id={`${panelId}-subtitle`}
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.5, overflowWrap: "anywhere", lineHeight: 1.45 }}
              >
                {requirementId
                  ? (names.get(requirementId) ?? "Selected test")
                  : "Current assessment"}
              </Typography>
            </Box>
          </Stack>
          <IconButton
            aria-label="Close review history"
            onClick={() => setExpanded(false)}
            sx={{ position: "absolute", right: 16, top: 16 }}
          >
            <CloseRoundedIcon />
          </IconButton>
        </DialogTitle>
        {requirementId && (
          <Tabs
            value={historyTab}
            onChange={(_, value: "current" | "related") => setHistoryTab(value)}
            aria-label="Review history scope"
            variant="fullWidth"
            sx={{ px: { xs: 1, sm: 2 } }}
          >
            <Tab
              value="current"
              label="This assessment"
              id={`${panelId}-current-tab`}
              aria-controls={`${panelId}-current-panel`}
            />
            <Tab
              value="related"
              label="Related past reviews"
              id={`${panelId}-related-tab`}
              aria-controls={`${panelId}-related-panel`}
            />
          </Tabs>
        )}
        <DialogContent
          id={panelId}
          role="region"
          aria-label="Review history details"
          dividers
          sx={{
            px: { xs: 2, sm: 3 },
            py: 2.5,
            overscrollBehavior: "contain",
            borderColor: "#e9edf3",
          }}
        >
          {historyTab === "related" && requirementId ? (
            <Box
              id={`${panelId}-related-panel`}
              role="tabpanel"
              aria-labelledby={`${panelId}-related-tab`}
            >
              <RelatedReviewReferences
                key={JSON.stringify([
                  documentId,
                  report.comparison_id,
                  requirementId,
                  report.report_input_fingerprint,
                ])}
                documentId={documentId}
                comparisonId={report.comparison_id}
                requirementId={requirementId}
                inputFingerprint={report.report_input_fingerprint}
              />
            </Box>
          ) : (
            <Box
              id={`${panelId}-current-panel`}
              role={requirementId ? "tabpanel" : undefined}
              aria-labelledby={
                requirementId ? `${panelId}-current-tab` : undefined
              }
            >
              {pending ? (
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={1}
                  role="status"
                  sx={{ py: 5, justifyContent: "center" }}
                >
                  <CircularProgress size={16} />
                  <Typography variant="body2">
                    Loading review history…
                  </Typography>
                </Stack>
              ) : error ? (
                <Alert
                  severity="info"
                  action={
                    <Button
                      color="inherit"
                      size="small"
                      onClick={() => setAttempt((value) => value + 1)}
                    >
                      Retry
                    </Button>
                  }
                >
                  {error}
                </Alert>
              ) : visible?.length ? (
                <>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mb: 2.5 }}
                  >
                    {visible.length} saved{" "}
                    {visible.length === 1 ? "decision" : "decisions"} · Newest
                    first
                  </Typography>
                  <Box
                    component="ol"
                    aria-label="Saved assessment review entries"
                    sx={{
                      m: 0,
                      p: 0,
                      listStyle: "none",
                      display: "grid",
                    }}
                  >
                    {visible.map((event, index) => (
                      <Box
                        component="li"
                        key={event.review_id}
                        sx={{
                          display: "grid",
                          gridTemplateColumns: "18px minmax(0, 1fr)",
                          gap: 1.5,
                          overflowWrap: "anywhere",
                        }}
                      >
                        <Box
                          aria-hidden="true"
                          sx={{
                            position: "relative",
                            pt: 0.5,
                            "&::after":
                              index < visible.length - 1
                                ? {
                                    content: '""',
                                    position: "absolute",
                                    width: "1px",
                                    bgcolor: "#dce4ee",
                                    top: 18,
                                    bottom: 0,
                                    left: 6,
                                  }
                                : {},
                          }}
                        >
                          <Box
                            sx={{
                              width: 13,
                              height: 13,
                              borderRadius: "50%",
                              bgcolor: index === 0 ? "#4775b8" : "#fff",
                              border: "2px solid",
                              borderColor: index === 0 ? "#4775b8" : "#c2cedd",
                            }}
                          />
                        </Box>
                        <Box
                          sx={{
                            minWidth: 0,
                            pb: index < visible.length - 1 ? 3 : 0,
                          }}
                        >
                          {!requirementId && (
                            <Typography variant="body2" fontWeight={650}>
                              {names.get(event.requirement_id) ?? "Saved test"}
                            </Typography>
                          )}
                          <Stack
                            direction="row"
                            alignItems="flex-start"
                            justifyContent="space-between"
                            gap={1}
                          >
                            <Box sx={{ minWidth: 0 }}>
                              <Typography
                                variant="body2"
                                fontWeight={700}
                                sx={{ fontSize: 14, lineHeight: 1.5 }}
                              >
                                {decisionLabels[event.decision] ??
                                  "Saved decision"}
                              </Typography>
                              {index === 0 && (
                                <Typography
                                  variant="caption"
                                  color="primary.main"
                                  fontWeight={650}
                                >
                                  Latest
                                </Typography>
                              )}
                            </Box>
                            <Chip
                              size="small"
                              label={
                                event.review_status === "accepted"
                                  ? "Reviewed"
                                  : "Needs review"
                              }
                              sx={{
                                height: 23,
                                fontSize: 11,
                                flexShrink: 0,
                                bgcolor:
                                  event.review_status === "accepted"
                                    ? "#eaf5ef"
                                    : "#fff3db",
                                color:
                                  event.review_status === "accepted"
                                    ? "#2c6b4e"
                                    : "#936315",
                                "& .MuiChip-label": { px: 1 },
                              }}
                            />
                          </Stack>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            display="block"
                            sx={{ mt: 0.75, lineHeight: 1.5 }}
                          >
                            <Box component="span" title={event.actor}>
                              {reviewerLabel(event, session?.user)}
                            </Box>
                            {" · "}
                            <Box component="time" dateTime={event.created_at}>
                              {dateLabel(event.created_at)}
                            </Box>
                          </Typography>
                          {event.note && (
                            <Typography
                              variant="body2"
                              sx={{
                                mt: 1,
                                px: 1.5,
                                py: 1.25,
                                borderRadius: 1.25,
                                bgcolor: "#f5f7fa",
                                whiteSpace: "pre-wrap",
                                lineHeight: 1.65,
                              }}
                            >
                              {event.note}
                            </Typography>
                          )}
                          {event.carried_from_review_id && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              display="block"
                              sx={{ mt: 0.5 }}
                            >
                              Review carried forward from a compatible saved
                              assessment.
                            </Typography>
                          )}
                        </Box>
                      </Box>
                    ))}
                  </Box>
                </>
              ) : (
                <Box sx={{ py: 4, textAlign: "center" }}>
                  <Typography variant="body2" fontWeight={650}>
                    No saved decisions yet
                  </Typography>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mt: 0.75 }}
                  >
                    The first saved review for this assessment will appear here.
                  </Typography>
                </Box>
              )}
            </Box>
          )}
        </DialogContent>
        <DialogActions
          sx={{
            px: { xs: 2, sm: 3 },
            py: 1.5,
            justifyContent: "space-between",
          }}
        >
          {historyTab === "current" &&
          !pending &&
          !error &&
          Boolean(events?.length) ? (
            <Link
              href={`/api/comparisons/${encodeURIComponent(documentId)}/reviews?download=true`}
              variant="caption"
              underline="hover"
              title="Download all reviews for the current assessment"
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.75,
                color: "text.secondary",
                fontWeight: 600,
              }}
            >
              <FileDownloadOutlinedIcon sx={{ fontSize: 17 }} />
              Download review history
            </Link>
          ) : (
            <Box />
          )}
          <Button onClick={() => setExpanded(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
