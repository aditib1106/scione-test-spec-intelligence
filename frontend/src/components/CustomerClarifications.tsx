import { useEffect, useId, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import QuestionAnswerOutlinedIcon from "@mui/icons-material/QuestionAnswerOutlined";
import { ApiError, request } from "../api";
import type {
  ComparisonData,
  CustomerClarificationEvent,
  CustomerClarificationThread,
} from "../comparisonTypes";

interface Props {
  documentId: string;
  report: ComparisonData;
  requirementId: string;
  onSaved: (updated: ComparisonData) => void;
}

interface Draft {
  event_kind: CustomerClarificationEvent["event_kind"];
  question_id?: string;
  text: string;
  contact: string;
}

function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Keep drafts and late responses confined to one source, test and assessment. */
export default function CustomerClarifications(props: Props) {
  return (
    <ClarificationPanel
      key={JSON.stringify([
        props.documentId,
        props.report.source_run_id,
        props.report.comparison_id,
        props.report.report_input_fingerprint,
        props.requirementId,
      ])}
      {...props}
    />
  );
}

function ClarificationPanel({
  documentId,
  report,
  requirementId,
  onSaved,
}: Props) {
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);
  const [thread, setThread] = useState<CustomerClarificationThread | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [readError, setReadError] = useState("");
  const [writeError, setWriteError] = useState("");
  const [reload, setReload] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const submitting = useRef(false);
  const writeController = useRef<AbortController | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const textInputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(
    null,
  );
  const attempt = useRef<{ id: string; version: number } | null>(null);
  const revision =
    report.customer_clarifications?.[requirementId]?.version ?? 0;
  const path = `/api/comparisons/${encodeURIComponent(documentId)}/clarifications`;

  function validThread(value: CustomerClarificationThread) {
    return Boolean(
      value &&
      value.document_id === documentId &&
      value.source_run_id === report.source_run_id &&
      value.comparison_id === report.comparison_id &&
      value.input_fingerprint === report.report_input_fingerprint &&
      value.requirement_id === requirementId &&
      Number.isInteger(value.version) &&
      Array.isArray(value.events) &&
      value.events.every(
        (event) =>
          event &&
          event.document_id === documentId &&
          event.source_run_id === report.source_run_id &&
          event.requirement_id === requirementId,
      ),
    );
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      writeController.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!expanded || !draft) return;
    const frame = window.requestAnimationFrame(() => {
      formRef.current?.scrollIntoView({ block: "start", behavior: "auto" });
      textInputRef.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [expanded, draft?.event_kind, draft?.question_id]);

  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    setLoading(true);
    setReadError("");
    setThread(null);
    const query = new URLSearchParams({
      requirement_id: requirementId,
      comparison_id: report.comparison_id,
      input_fingerprint: report.report_input_fingerprint ?? "",
    });
    void request<CustomerClarificationThread>(`${path}?${query}`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (controller.signal.aborted) return;
        if (!validThread(value))
          throw new Error(
            "The assessment changed. Refresh the report to view its clarifications.",
          );
        setThread(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setReadError(
            reason instanceof Error
              ? reason.message
              : "Clarifications could not be loaded.",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [
    expanded,
    revision,
    reload,
    path,
    requirementId,
    report.comparison_id,
    report.report_input_fingerprint,
    report.source_run_id,
  ]);

  function begin(question?: CustomerClarificationEvent) {
    attempt.current = null;
    setWriteError("");
    setDraft({
      event_kind: question ? "customer_reply" : "question",
      ...(question ? { question_id: question.event_id } : {}),
      text: "",
      contact: question?.contact ?? "",
    });
  }

  function updateDraft(field: "text" | "contact", value: string) {
    if (!draft || busy || draft[field] === value) return;
    attempt.current = null;
    setDraft({ ...draft, [field]: value });
    setWriteError("");
  }

  async function save() {
    if (submitting.current || !thread || !draft || loading) return;
    if (!draft.text.trim()) {
      setWriteError(
        draft.event_kind === "question"
          ? "Enter the question to record."
          : "Enter the customer's reply to record.",
      );
      return;
    }
    const token = attempt.current ?? {
      id: crypto.randomUUID(),
      version: thread.version,
    };
    attempt.current = token;
    const controller = new AbortController();
    writeController.current = controller;
    submitting.current = true;
    setBusy(true);
    setWriteError("");
    try {
      const updated = await request<
        CustomerClarificationThread & { comparison: ComparisonData }
      >(path, {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          comparison_id: report.comparison_id,
          input_fingerprint: report.report_input_fingerprint,
          requirement_id: requirementId,
          expected_version: token.version,
          client_request_id: token.id,
          event_kind: draft.event_kind,
          text: draft.text.trim(),
          contact: draft.contact.trim(),
          ...(draft.question_id ? { question_id: draft.question_id } : {}),
        }),
      });
      if (!mounted.current || controller.signal.aborted) return;
      if (
        !validThread(updated) ||
        updated.comparison?.document?.document_id !== documentId ||
        updated.comparison?.comparison_id !== report.comparison_id ||
        updated.comparison?.source_run_id !== report.source_run_id ||
        updated.comparison?.report_input_fingerprint !==
          report.report_input_fingerprint
      )
        throw new Error(
          "The assessment changed. Your draft is kept; refresh before saving again.",
        );
      setThread(updated);
      setDraft(null);
      attempt.current = null;
      onSaved(updated.comparison);
    } catch (reason) {
      if (!mounted.current || controller.signal.aborted) return;
      if (reason instanceof ApiError && reason.status === 409) {
        attempt.current = null;
        setReload((value) => value + 1);
        setWriteError(
          "The saved conversation or assessment changed. Your draft is kept. Check the refreshed conversation before saving again.",
        );
      } else {
        setWriteError(
          reason instanceof Error
            ? reason.message
            : "The record could not be saved. Your draft is kept; try saving again.",
        );
      }
    } finally {
      if (mounted.current && !controller.signal.aborted) {
        submitting.current = false;
        setBusy(false);
      }
    }
  }

  const events = [...(thread?.events ?? [])].sort(
    (a, b) => a.version - b.version,
  );
  const questions = events.filter((event) => event.event_kind === "question");
  const selectedQuestion = draft?.question_id
    ? questions.find((question) => question.event_id === draft.question_id)
    : null;
  return (
    <>
      <Button
        size="small"
        color="inherit"
        aria-haspopup="dialog"
        aria-expanded={expanded}
        aria-controls={`${panelId}-dialog`}
        startIcon={<QuestionAnswerOutlinedIcon fontSize="small" />}
        onClick={() => setExpanded(true)}
        sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
      >
        Customer clarifications
      </Button>
      <Dialog
        open={expanded}
        onClose={(_, reason) => {
          if (!busy && reason !== "backdropClick") setExpanded(false);
        }}
        disableEscapeKeyDown={busy}
        aria-labelledby={`${panelId}-title`}
        aria-describedby={`${panelId}-subtitle`}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          id: `${panelId}-dialog`,
          sx: { maxHeight: "min(85dvh, 800px)" },
        }}
      >
        <DialogTitle id={`${panelId}-heading`} sx={{ pr: 7, pb: 1.5 }}>
          <Box component="span" id={`${panelId}-title`}>
            Customer clarifications
          </Box>
          <IconButton
            aria-label="Close customer clarifications"
            disabled={busy}
            onClick={() => setExpanded(false)}
            sx={{ position: "absolute", right: 16, top: 16 }}
          >
            <CloseRoundedIcon />
          </IconButton>
          <Typography
            id={`${panelId}-subtitle`}
            variant="body2"
            color="text.secondary"
            sx={{ mt: 0.75 }}
          >
            {report.requirements.find(
              (item) => item.requirement_id === requirementId,
            )?.test_name ?? "Selected test"}
          </Typography>
        </DialogTitle>
        <DialogContent
          id={panelId}
          role="region"
          aria-label="Customer clarification details"
          dividers
          sx={{ px: { xs: 2, sm: 3 }, py: 2, overscrollBehavior: "contain" }}
        >
          <Typography
            variant="caption"
            color="text.secondary"
            display="block"
            sx={{ mb: 1 }}
          >
            Lab staff can record questions and replies from email or phone
            calls. Nothing is sent from here, and recorded replies do not change
            the comparison.
          </Typography>
          {loading ? (
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              role="status"
              sx={{ py: 1 }}
            >
              <CircularProgress size={16} />
              <Typography variant="body2">Loading clarifications…</Typography>
            </Stack>
          ) : readError ? (
            <Alert
              severity="info"
              action={
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => setReload((value) => value + 1)}
                >
                  Retry
                </Button>
              }
            >
              {readError}
            </Alert>
          ) : (
            <>
              {questions.length ? (
                <>
                  {!draft && (
                    <Stack
                      direction="row"
                      alignItems="center"
                      justifyContent="space-between"
                      gap={1}
                      sx={{
                        mb: 1.5,
                        py: 0.5,
                        position: "sticky",
                        top: -16,
                        bgcolor: "#fff",
                        zIndex: 1,
                      }}
                    >
                      <Typography variant="body2" color="text.secondary">
                        {questions.length} recorded{" "}
                        {questions.length === 1 ? "question" : "questions"}
                      </Typography>
                      <Button
                        size="small"
                        variant="contained"
                        disabled={busy || !thread}
                        onClick={() => begin()}
                      >
                        Record question
                      </Button>
                    </Stack>
                  )}
                  <Box
                    component="ol"
                    aria-label="Recorded customer questions"
                    sx={{
                      m: 0,
                      p: 0,
                      listStyle: "none",
                      display: "grid",
                      gap: 1.5,
                    }}
                  >
                    {questions.map((question) => (
                      <Box
                        component="li"
                        key={question.event_id}
                        sx={{
                          p: 1.75,
                          border: "1px solid",
                          borderColor: "divider",
                          borderRadius: 2,
                          bgcolor: "#f8faff",
                          overflowWrap: "anywhere",
                        }}
                      >
                        <EventDetails
                          event={question}
                          comparisonId={report.comparison_id}
                        />
                        {events
                          .filter(
                            (event) =>
                              event.event_kind === "customer_reply" &&
                              event.question_id === question.event_id,
                          )
                          .map((reply) => (
                            <Box
                              key={reply.event_id}
                              sx={{
                                mt: 1.25,
                                ml: { xs: 0, sm: 1 },
                                p: 1.5,
                                borderLeft: "3px solid #94b7a6",
                                borderRadius: 1,
                                bgcolor: "#edf6f1",
                              }}
                            >
                              <EventDetails
                                event={reply}
                                comparisonId={report.comparison_id}
                              />
                            </Box>
                          ))}
                        <Button
                          size="small"
                          sx={{ mt: 0.75 }}
                          disabled={busy || Boolean(draft)}
                          onClick={() => begin(question)}
                          aria-label={`Record reply to question ${question.version}`}
                        >
                          Record reply
                        </Button>
                      </Box>
                    ))}
                  </Box>
                </>
              ) : (
                <Box
                  sx={{
                    p: 2,
                    border: "1px solid",
                    borderColor: "#dce5f1",
                    borderRadius: 2,
                    bgcolor: "#f8faff",
                  }}
                >
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ py: 1 }}
                  >
                    No customer questions recorded for this test yet.
                  </Typography>
                  {!draft && (
                    <Button
                      size="small"
                      variant="contained"
                      disabled={busy || !thread}
                      onClick={() => begin()}
                    >
                      Record question
                    </Button>
                  )}
                </Box>
              )}
            </>
          )}
          {draft && (
            <Box
              component="form"
              ref={formRef}
              aria-label={
                draft.event_kind === "question"
                  ? "Record question"
                  : "Record customer reply"
              }
              onSubmit={(event) => {
                event.preventDefault();
                void save();
              }}
              sx={{
                mt: 2,
                p: 2,
                border: "1px solid",
                borderColor: "#c8d8ed",
                borderRadius: 2,
                bgcolor: "#f8faff",
              }}
            >
              <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>
                {draft.event_kind === "question"
                  ? "Record a question"
                  : "Record the customer's reply"}
              </Typography>
              {selectedQuestion && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  display="block"
                  sx={{ mb: 1, overflowWrap: "anywhere" }}
                >
                  Reply to: {selectedQuestion.text}
                </Typography>
              )}
              <Stack
                component="fieldset"
                disabled={busy}
                inert={busy}
                gap={1.5}
                sx={{ m: 0, p: 0, border: 0, minWidth: 0 }}
              >
                {writeError && <Alert severity="warning">{writeError}</Alert>}
                <TextField
                  inputRef={textInputRef}
                  label={
                    draft.event_kind === "question"
                      ? "Question"
                      : "Customer reply"
                  }
                  value={draft.text}
                  onChange={(event) => updateDraft("text", event.target.value)}
                  multiline
                  minRows={2}
                  maxRows={5}
                  required
                  fullWidth
                  size="small"
                  inputProps={{ maxLength: 4000 }}
                />
                <TextField
                  label="Contact (optional)"
                  value={draft.contact}
                  onChange={(event) =>
                    updateDraft("contact", event.target.value)
                  }
                  fullWidth
                  size="small"
                  inputProps={{ maxLength: 300 }}
                />
              </Stack>
              <Stack
                direction="row"
                justifyContent="flex-end"
                gap={1}
                sx={{ mt: 1 }}
              >
                <Button
                  disabled={busy}
                  onClick={() => {
                    setDraft(null);
                    attempt.current = null;
                    setWriteError("");
                  }}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="contained"
                  size="small"
                  disabled={busy || loading || !thread || !draft.text.trim()}
                >
                  {busy
                    ? "Saving…"
                    : draft.event_kind === "question"
                      ? "Save question"
                      : "Save reply"}
                </Button>
              </Stack>
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, py: 1.5 }}>
          <Button disabled={busy} onClick={() => setExpanded(false)}>
            Close
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

function EventDetails({
  event,
  comparisonId,
}: {
  event: CustomerClarificationEvent;
  comparisonId: string;
}) {
  return (
    <>
      <Typography variant="body2" fontWeight={650}>
        {event.event_kind === "question" ? "Question" : "Customer reply"}
      </Typography>
      <Typography variant="body2" sx={{ mt: 0.25, whiteSpace: "pre-wrap" }}>
        {event.text}
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        display="block"
        sx={{ mt: 0.5 }}
      >
        Recorded by {event.actor_label || event.actor || "Lab user"}
        {" · "}
        <Box component="time" dateTime={event.created_at}>
          {dateLabel(event.created_at)}
        </Box>
      </Typography>
      {event.contact && (
        <Typography variant="caption" color="text.secondary" display="block">
          Contact: {event.contact}
        </Typography>
      )}
      {event.comparison_id !== comparisonId && (
        <Typography variant="caption" color="text.secondary" display="block">
          From an earlier assessment of this source extraction.
        </Typography>
      )}
    </>
  );
}
