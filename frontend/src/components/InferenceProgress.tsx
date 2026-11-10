import { useEffect, useState } from "react";
import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";

/** Indeterminate progress: no invented percentage or unobserved provider stages. */
export default function InferenceProgress({
  title,
  detail,
}: {
  title: string;
  detail: string;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, []);
  return (
    <Box
      role="status"
      aria-label={title}
      sx={{
        p: 2,
        border: "1px solid #cbdcf8",
        borderRadius: 2,
        bgcolor: "#f0f6ff",
      }}
    >
      <Stack direction="row" gap={1.25} alignItems="center" sx={{ mb: 1.25 }}>
        <AutoAwesomeOutlinedIcon sx={{ color: "primary.main" }} />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography fontWeight={700} variant="body2">
            {title}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ overflowWrap: "anywhere" }}
          >
            {detail}
          </Typography>
        </Box>
        <Typography
          variant="caption"
          color="text.secondary"
          aria-hidden
          sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
        >
          {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
        </Typography>
      </Stack>
      <LinearProgress
        aria-label="AI request in progress"
        sx={{ height: 5, borderRadius: 5 }}
      />
    </Box>
  );
}
