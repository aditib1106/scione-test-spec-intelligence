import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import FitScreenRoundedIcon from "@mui/icons-material/FitScreenRounded";
import OpenInFullRoundedIcon from "@mui/icons-material/OpenInFullRounded";
import OpenInNewRoundedIcon from "@mui/icons-material/OpenInNewRounded";
import ZoomInRoundedIcon from "@mui/icons-material/ZoomInRounded";
import ZoomOutRoundedIcon from "@mui/icons-material/ZoomOutRounded";

interface Props {
  imageUrl: string;
  alt: string;
  title: string;
  loading?: boolean;
  error?: string;
  documentUrl?: string;
  navigation?: ReactNode;
  height?: number | string;
}

/** Width-based PDF reading: tall pages scroll rather than shrink to a thumbnail. */
export default function PdfPageViewer({
  imageUrl,
  alt,
  title,
  loading = false,
  error,
  documentUrl,
  navigation,
  height = "70vh",
}: Props) {
  const [zoom, setZoom] = useState(100);
  const [expanded, setExpanded] = useState(false);
  const normalViewport = useRef<HTMLDivElement>(null);
  const expandedViewport = useRef<HTMLDivElement>(null);

  useEffect(() => {
    normalViewport.current?.scrollTo({ top: 0, left: 0 });
    expandedViewport.current?.scrollTo({ top: 0, left: 0 });
  }, [imageUrl]);

  const contents = (fullscreen: boolean) => (
    <Stack
      sx={{
        height: fullscreen ? "100dvh" : height,
        minHeight: fullscreen ? 0 : 320,
        minWidth: 0,
        p: fullscreen ? { xs: 1.5, sm: 2.5 } : 0,
        bgcolor: "#fff",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={1}
        sx={{ mb: 1, flexShrink: 0 }}
      >
        <Typography variant="subtitle1" fontWeight={700}>
          {title}
        </Typography>
        <Stack direction="row" alignItems="center" spacing={0.25}>
          {documentUrl && (
            <Tooltip title="Open original PDF in a new tab">
              <IconButton
                component="a"
                href={documentUrl}
                target="_blank"
                rel="noreferrer"
                size="small"
                aria-label="Open original PDF"
              >
                <OpenInNewRoundedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title={fullscreen ? "Close expanded PDF" : "Expand PDF"}>
            <IconButton
              size="small"
              aria-label={fullscreen ? "Close expanded PDF" : "Expand PDF"}
              onClick={() => setExpanded(!fullscreen)}
            >
              {fullscreen ? <CloseRoundedIcon /> : <OpenInFullRoundedIcon />}
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>
      <Stack
        direction="row"
        useFlexGap
        flexWrap="wrap"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
        sx={{ pb: 1.25, flexShrink: 0 }}
      >
        {navigation && <Box sx={{ minWidth: 0 }}>{navigation}</Box>}
        <Stack direction="row" alignItems="center" spacing={0.25}>
          <Tooltip title="Zoom out">
            <span>
              <IconButton
                size="small"
                aria-label="Zoom out"
                disabled={zoom <= 75}
                onClick={() => setZoom((value) => Math.max(75, value - 25))}
              >
                <ZoomOutRoundedIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Typography
            variant="caption"
            aria-live="polite"
            sx={{
              minWidth: 38,
              textAlign: "center",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {zoom}%
          </Typography>
          <Tooltip title="Zoom in">
            <span>
              <IconButton
                size="small"
                aria-label="Zoom in"
                disabled={zoom >= 250}
                onClick={() => setZoom((value) => Math.min(250, value + 25))}
              >
                <ZoomInRoundedIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Button
            size="small"
            startIcon={<FitScreenRoundedIcon fontSize="small" />}
            onClick={() => setZoom(100)}
            sx={{ ml: 0.5, whiteSpace: "nowrap" }}
          >
            Fit width
          </Button>
        </Stack>
      </Stack>
      {error && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          {error}
        </Alert>
      )}
      <Box
        ref={fullscreen ? expandedViewport : normalViewport}
        role="region"
        aria-label={`${title} scroll area`}
        tabIndex={0}
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          bgcolor: "#e8edf4",
          border: "1px solid #dce3ed",
          borderRadius: 1.5,
          overscrollBehavior: "contain",
          position: "relative",
          scrollbarGutter: "stable",
        }}
      >
        {loading && (
          <Box
            sx={{ display: "grid", placeItems: "center", minHeight: 220 }}
            aria-label="Loading PDF page"
          >
            <CircularProgress size={28} />
          </Box>
        )}
        {!loading && imageUrl && (
          <Box
            sx={{
              width: `${zoom}%`,
              minWidth: `${zoom}%`,
              mx: zoom <= 100 ? "auto" : 0,
              p: { xs: 0.5, sm: 1 },
              boxSizing: "border-box",
            }}
          >
            <Box
              component="img"
              src={imageUrl}
              alt={alt}
              draggable={false}
              sx={{
                display: "block",
                width: "100%",
                maxWidth: "none",
                height: "auto",
                bgcolor: "#fff",
                boxShadow: "0 2px 12px rgba(20,35,70,.12)",
              }}
            />
          </Box>
        )}
        {!loading && !imageUrl && !error && (
          <Typography color="text.secondary" sx={{ p: 4, textAlign: "center" }}>
            Select a page to preview the PDF.
          </Typography>
        )}
      </Box>
    </Stack>
  );

  return (
    <>
      {contents(false)}
      <Dialog fullScreen open={expanded} onClose={() => setExpanded(false)}>
        {contents(true)}
      </Dialog>
    </>
  );
}
