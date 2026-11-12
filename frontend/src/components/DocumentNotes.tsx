import { useEffect, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import CheckRoundedIcon from "@mui/icons-material/CheckRounded";
import { ApiError, request, reviewDocumentNotes } from "../api";
import type { DocumentNotesReview } from "../documentNotesTypes";

/** Whole-document notes are distinct from test confirmation and capability decisions. */
export default function DocumentNotes({
  documentId,
  review,
  sourceReviewUrl,
  onUpdated,
}: {
  documentId: string;
  review: DocumentNotesReview;
  sourceReviewUrl?: string;
  onUpdated: (next: DocumentNotesReview) => void;
}) {
  const [current, setCurrent] = useState(review);
  const [expanded, setExpanded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const alive = useRef(true);
  const fingerprint = useRef(review.source_fingerprint);
  fingerprint.current = review.source_fingerprint;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    setCurrent(review);
    setError("");
    setStale(false);
  }, [review]);

  const update = async (noteIds: string[], checked: boolean) => {
    if (saving || !noteIds.length) return;
    const source = current.source_fingerprint;
    setSaving(true);
    setError("");
    try {
      const next = await reviewDocumentNotes(documentId, {
        source_fingerprint: source,
        expected_version: current.version,
        note_ids: noteIds,
        reviewed: checked,
      });
      if (!alive.current || fingerprint.current !== source) return;
      setCurrent(next);
      onUpdated(next);
    } catch (reason) {
      if (!alive.current || fingerprint.current !== source) return;
      const changed = reason instanceof ApiError && reason.status === 409;
      setStale(changed);
      setError(
        changed
          ? "These notes have changed. Load the current notes before saving your review."
          : "Your note review could not be saved. Please try again.",
      );
    } finally {
      if (alive.current) setSaving(false);
    }
  };

  const reload = async () => {
    setSaving(true);
    try {
      const saved = await request<{
        document_notes_review?: DocumentNotesReview;
      }>(`/api/specifications/${encodeURIComponent(documentId)}`);
      if (!alive.current || !saved.document_notes_review) return;
      setCurrent(saved.document_notes_review);
      onUpdated(saved.document_notes_review);
      setStale(false);
      setError("");
    } catch {
      if (alive.current)
        setError("The current notes could not be loaded. Please try again.");
    } finally {
      if (alive.current) setSaving(false);
    }
  };

  if (!current.notes.length) return null;
  const pending = current.notes.filter((note) => !note.reviewed);
  return (
    <Accordion
      expanded={expanded}
      onChange={(_event, next) => setExpanded(next)}
      disableGutters
      elevation={0}
      sx={{
        border: "1px solid #dce4ee",
        borderRadius: "12px !important",
        bgcolor: "#fff",
        "&:before": { display: "none" },
        flexShrink: 0,
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreRoundedIcon />}
        sx={{ minHeight: 48, "& .MuiAccordionSummary-content": { my: 1 } }}
      >
        <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
          <DescriptionOutlinedIcon sx={{ color: "#58708d", fontSize: 20 }} />
          <Typography variant="body2" fontWeight={700}>
            Document notes
          </Typography>
          {pending.length ? (
            <Typography variant="body2" color="text.secondary">
              {pending.length} to check
            </Typography>
          ) : (
            <Chip
              size="small"
              icon={<CheckRoundedIcon />}
              label="Reviewed"
              sx={{ height: 24, bgcolor: "#eaf5ef", color: "#236647" }}
            />
          )}
        </Stack>
      </AccordionSummary>
      <AccordionDetails sx={{ pt: 0, px: 2.5, pb: 2 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          These notes concern the whole document. Checking them records your
          review; test confirmations and lab findings stay unchanged.
        </Typography>
        {error && (
          <Alert
            severity="warning"
            sx={{ mb: 1.5 }}
            action={
              stale ? (
                <Button
                  color="inherit"
                  size="small"
                  onClick={reload}
                  disabled={saving}
                >
                  Load current notes
                </Button>
              ) : undefined
            }
          >
            {error}
          </Alert>
        )}
        <Stack gap={1.25}>
          {current.notes.map((note) => (
            <Box
              key={note.id}
              sx={{
                p: 1.5,
                border: "1px solid #e6ebf2",
                borderRadius: 1.5,
                bgcolor: note.reviewed ? "#fafcfb" : "#f8fafc",
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                justifyContent="space-between"
                gap={1}
              >
                <Typography variant="body2" fontWeight={650}>
                  {note.title}
                </Typography>
                {note.reviewed ? (
                  <Stack direction="row" gap={0.5} alignItems="center">
                    <Chip
                      size="small"
                      label="Checked"
                      icon={<CheckRoundedIcon />}
                      sx={{ height: 24, bgcolor: "#eaf5ef", color: "#236647" }}
                    />
                    <Button
                      size="small"
                      disabled={saving || stale}
                      onClick={() => update([note.id], false)}
                    >
                      Reopen
                    </Button>
                  </Stack>
                ) : (
                  <Button
                    size="small"
                    variant="outlined"
                    disabled={saving || stale}
                    startIcon={
                      saving ? (
                        <CircularProgress size={14} />
                      ) : (
                        <CheckRoundedIcon />
                      )
                    }
                    onClick={() => update([note.id], true)}
                  >
                    Mark checked
                  </Button>
                )}
              </Stack>
              {note.quote && (
                <Typography
                  variant="body2"
                  sx={{ mt: 0.75, overflowWrap: "anywhere" }}
                >
                  {note.quote}
                </Typography>
              )}
              {note.detail && (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5, overflowWrap: "anywhere" }}
                >
                  {note.detail}
                </Typography>
              )}
              {note.page && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 0.75 }}
                >
                  Page {note.page}
                </Typography>
              )}
            </Box>
          ))}
        </Stack>
        {(sourceReviewUrl || pending.length > 1) && (
          <Stack
            direction="row"
            gap={1}
            justifyContent="flex-end"
            flexWrap="wrap"
            sx={{ mt: 1.5 }}
          >
            {sourceReviewUrl && (
              <Button component={RouterLink} to={sourceReviewUrl} size="small">
                Open specification
              </Button>
            )}
            {pending.length > 1 && (
              <Button
                variant="outlined"
                size="small"
                disabled={saving || stale}
                onClick={() =>
                  update(
                    pending.map((note) => note.id),
                    true,
                  )
                }
              >
                Mark notes checked
              </Button>
            )}
          </Stack>
        )}
      </AccordionDetails>
    </Accordion>
  );
}
