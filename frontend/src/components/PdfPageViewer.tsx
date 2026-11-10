import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
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
  documentLabel?: string;
  navigation?: ReactNode;
  height?: number | string;
  focusY?: number | null;
  focusRequest?: number | string;
  initialFit?: "width" | "page";
}

/** Source PDFs fit width; exported reports can fit a complete page by default. */
export default function PdfPageViewer({
  imageUrl,
  alt,
  title,
  loading = false,
  error,
  documentUrl,
  documentLabel = "Open original PDF",
  navigation,
  height = "70vh",
  focusY,
  focusRequest,
  initialFit = "width",
}: Props) {
  const [zoom, setZoom] = useState(100);
  const [fit, setFit] = useState(initialFit);
  const [aspectRatio, setAspectRatio] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [expandedReady, setExpandedReady] = useState(false);
  const [viewportWidths, setViewportWidths] = useState({
    normal: { width: 0, height: 0 },
    expanded: { width: 0, height: 0 },
  });
  const normalViewport = useRef<HTMLDivElement>(null);
  const expandedViewport = useRef<HTMLDivElement>(null);
  const normalImage = useRef<HTMLImageElement>(null);
  const expandedImage = useRef<HTMLImageElement>(null);
  const lastNormalFocus = useRef<number | string | undefined>(undefined);
  const lastExpandedFocus = useRef<number | string | undefined>(undefined);

  useLayoutEffect(() => {
    const observe = (
      node: HTMLDivElement | null,
      key: "normal" | "expanded",
    ) => {
      if (!node) return null;
      const measure = () =>
        setViewportWidths((previous) => {
          const width = node.clientWidth;
          const height = node.clientHeight;
          return previous[key].width === width &&
            previous[key].height === height
            ? previous
            : { ...previous, [key]: { width, height } };
        });
      measure();
      const observer = new ResizeObserver(measure);
      observer.observe(node);
      return observer;
    };
    const normal = observe(normalViewport.current, "normal");
    const fullscreen = observe(expandedViewport.current, "expanded");
    return () => {
      normal?.disconnect();
      fullscreen?.disconnect();
    };
  }, [expanded, expandedReady, imageUrl, loading]);

  const fitView = (fullscreen: boolean, mode: "width" | "page") => {
    setFit(mode);
    setZoom(100);
    const viewport = (fullscreen ? expandedViewport : normalViewport).current;
    if (viewport) {
      setViewportWidths((previous) => ({
        ...previous,
        [fullscreen ? "expanded" : "normal"]: {
          width: viewport.clientWidth,
          height: viewport.clientHeight,
        },
      }));
      viewport.scrollTo({ top: 0, left: 0 });
    }
  };

  useLayoutEffect(() => {
    normalViewport.current?.scrollTo({ top: 0, left: 0 });
    expandedViewport.current?.scrollTo({ top: 0, left: 0 });
  }, [imageUrl]);

  const focusSource = useCallback(
    (fullscreen: boolean) => {
      const viewport = (fullscreen ? expandedViewport : normalViewport).current;
      const sourceImage = (fullscreen ? expandedImage : normalImage).current;
      const lastFocus = fullscreen ? lastExpandedFocus : lastNormalFocus;
      if (
        loading ||
        focusRequest === undefined ||
        lastFocus.current === focusRequest ||
        focusY == null ||
        !Number.isFinite(focusY) ||
        focusY < 0 ||
        focusY > 1 ||
        !viewport ||
        !sourceImage?.complete ||
        !sourceImage.naturalHeight ||
        sourceImage.currentSrc !== sourceImage.src ||
        !viewport.clientHeight
      )
        return;
      const imageBounds = sourceImage.getBoundingClientRect();
      const viewportBounds = viewport.getBoundingClientRect();
      const imageTop =
        viewport.scrollTop + imageBounds.top - viewportBounds.top;
      viewport.scrollTo({
        top: Math.max(
          0,
          imageTop + focusY * imageBounds.height - viewport.clientHeight * 0.45,
        ),
      });
      lastFocus.current = focusRequest;
    },
    [loading, focusRequest, focusY],
  );

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      focusSource(false);
      if (expanded) focusSource(true);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusSource, imageUrl, expanded]);

  const contents = (fullscreen: boolean) => {
    const viewport = viewportWidths[fullscreen ? "expanded" : "normal"];
    const fittedWidth =
      fit === "page" && aspectRatio && viewport.height
        ? Math.min(
            viewport.width,
            Math.max(0, viewport.height - 16) * aspectRatio,
          )
        : viewport.width;
    return (
      <Stack
        sx={{
          height: fullscreen ? "100dvh" : height,
          minHeight: fullscreen || height === "100%" ? 0 : 320,
          flex: !fullscreen && height === "100%" ? 1 : undefined,
          overflow: "hidden",
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
              <Tooltip title={`${documentLabel} in a new tab`}>
                <IconButton
                  component="a"
                  href={documentUrl}
                  target="_blank"
                  rel="noreferrer"
                  size="small"
                  aria-label={documentLabel}
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
              {zoom === 100 && fit === "page" ? "Page" : `${zoom}%`}
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
              onClick={() => fitView(fullscreen, "width")}
              aria-pressed={fit === "width" && zoom === 100}
              sx={{ ml: 0.5, whiteSpace: "nowrap" }}
            >
              Fit width
            </Button>
            <Button
              size="small"
              onClick={() => fitView(fullscreen, "page")}
              aria-pressed={fit === "page" && zoom === 100}
              sx={{ whiteSpace: "nowrap" }}
            >
              Fit page
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
                width: fittedWidth
                  ? `${(fittedWidth * zoom) / 100}px`
                  : `${zoom}%`,
                minWidth: 0,
                mx: zoom <= 100 ? "auto" : 0,
                p: { xs: 0.5, sm: 1 },
                boxSizing: "border-box",
              }}
            >
              <Box
                component="img"
                ref={fullscreen ? expandedImage : normalImage}
                src={imageUrl}
                onLoad={(event) => {
                  setAspectRatio(
                    event.currentTarget.naturalWidth /
                      event.currentTarget.naturalHeight,
                  );
                  focusSource(fullscreen);
                }}
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
            <Typography
              color="text.secondary"
              sx={{ p: 4, textAlign: "center" }}
            >
              Select a page to preview the PDF.
            </Typography>
          )}
        </Box>
      </Stack>
    );
  };

  return (
    <>
      {contents(false)}
      <Dialog
        fullScreen
        open={expanded}
        onClose={() => setExpanded(false)}
        slotProps={{
          transition: {
            onEntered: () => setExpandedReady(true),
            onExited: () => setExpandedReady(false),
          },
        }}
      >
        {contents(true)}
      </Dialog>
    </>
  );
}
