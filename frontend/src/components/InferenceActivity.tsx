import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import { Link } from "react-router-dom";
import {
  clientInferenceActivities,
  inferenceCompleted,
  request,
  type InferenceActivity,
} from "../api";

const Context = createContext<{ active: InferenceActivity[] }>({ active: [] });
export const useInferenceActivity = () => useContext(Context);

/** Follow accepted work across routes; the server remains the authority on reload. */
export function InferenceActivityProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [local, setLocal] = useState(clientInferenceActivities);
  const [server, setServer] = useState<InferenceActivity[]>([]);
  const previous = useRef<InferenceActivity[]>([]);
  const mounted = useRef(false);
  const polling = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    if (!mounted.current || polling.current) return;
    polling.current = true;
    const current = new AbortController();
    controller.current = current;
    try {
      const result = await request<{ active: InferenceActivity[] }>(
        "/api/operations",
        {
          signal: current.signal,
        },
      );
      if (!mounted.current || current.signal.aborted) return;
      const active = Array.isArray(result.active) ? result.active : [];
      const ongoing = new Set(active.map((item) => item.document_id));
      const localDocuments = new Set(
        clientInferenceActivities().map((item) => item.document_id),
      );
      const finished = previous.current.filter(
        (item) =>
          !ongoing.has(item.document_id) &&
          !localDocuments.has(item.document_id),
      );
      previous.current = active;
      setServer(active);
      for (const document of new Set(finished.map((item) => item.document_id)))
        inferenceCompleted(document);
    } catch {
      // A failed status read does not cancel/retry an accepted model operation.
      // Keep the last known activity until the connection can be checked again.
    } finally {
      if (controller.current === current) polling.current = false;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const changed = () => {
      setLocal(clientInferenceActivities());
      void refresh();
    };
    const focused = () => void refresh();
    window.addEventListener("scione:inference-activity", changed);
    window.addEventListener("focus", focused);
    void refresh();
    return () => {
      mounted.current = false;
      controller.current?.abort();
      controller.current = null;
      polling.current = false;
      window.removeEventListener("scione:inference-activity", changed);
      window.removeEventListener("focus", focused);
    };
  }, [refresh]);

  const active = useMemo(() => {
    const combined = new Map<string, InferenceActivity>();
    for (const item of [...local, ...server])
      combined.set(`${item.document_id}:${item.kind}`, item);
    return [...combined.values()];
  }, [local, server]);
  useEffect(() => {
    if (!active.length) return;
    const interval = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(interval);
  }, [active.length, refresh]);
  return <Context.Provider value={{ active }}>{children}</Context.Provider>;
}

export function InferenceActivityBanner() {
  const { active } = useInferenceActivity();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active.length) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active.length]);
  if (!active.length) return null;
  const item = active[0];
  const elapsed = Math.max(
    0,
    Math.round((now - Date.parse(item.started_at)) / 1000),
  );
  const route = item.kind === "extraction" ? "/specifications" : "/reports";
  return (
    <Alert
      severity="info"
      icon={<CircularProgress size={18} />}
      sx={{
        mx: { xs: 2, md: 4 },
        mt: 2,
        alignItems: "center",
        "& .MuiAlert-message": { width: "100%" },
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        gap={1.5}
        alignItems={{ sm: "center" }}
      >
        <Box sx={{ flex: 1 }}>
          <Typography variant="body2" fontWeight={650}>
            {item.kind === "extraction"
              ? "Reading your specification"
              : "Generating your report"}
            {Number.isFinite(elapsed) &&
              ` · ${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`}
            {active.length > 1 && ` · ${active.length} active tasks`}
          </Typography>
          <Typography variant="caption">
            You can browse other pages while this finishes.
          </Typography>
        </Box>
        <Button
          component={Link}
          to={`${route}?document=${item.document_id}`}
          size="small"
          variant="outlined"
        >
          View progress
        </Button>
      </Stack>
    </Alert>
  );
}
