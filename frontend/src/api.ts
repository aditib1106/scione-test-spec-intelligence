import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

let csrfToken: string | null = null;
let sessionEpoch = 0;
const activeRequests = new Set<AbortController>();
const resourceCache = new Map<string, { value: unknown; savedAt: number }>();
let resourceRevision = 0;
const CACHE_AGE_MS = 30_000;
const CACHE_ENTRIES = 12;

export interface InferenceActivity {
  operation_id: string;
  document_id: string;
  kind: "extraction" | "matching";
  started_at: string;
  elapsed_seconds: number;
  state: "running";
}
const pendingInference = new Map<string, InferenceActivity>();

export function clientInferenceActivities(): InferenceActivity[] {
  return [...pendingInference.values()];
}

function announceActivity() {
  window.dispatchEvent(new Event("scione:inference-activity"));
}

export function inferenceCompleted(documentId: string) {
  resourceCache.clear();
  resourceRevision += 1;
  window.dispatchEvent(
    new CustomEvent("scione:inference-completed", {
      detail: { documentId },
    }),
  );
}

function cachedResource<T>(path: string | null): T | null {
  if (!path) return null;
  const entry = resourceCache.get(path);
  if (!entry || Date.now() - entry.savedAt > CACHE_AGE_MS) return null;
  return structuredClone(entry.value) as T;
}

function rememberResource(path: string, value: unknown) {
  // Reuse navigation data only in memory. Never retain raw provider artifacts,
  // auth responses or another lab's data in a persistent browser cache.
  if (!/^\/api\/(workspace$|library(?:\/[a-f0-9]{16})?(?:\?|$))/.test(path))
    return;
  resourceCache.delete(path);
  resourceCache.set(path, {
    value: structuredClone(value),
    savedAt: Date.now(),
  });
  while (resourceCache.size > CACHE_ENTRIES)
    resourceCache.delete(resourceCache.keys().next().value!);
}

/** Clear every in-flight request before accepting another user's/lab's session. */
export function configureApiSession(token: string | null) {
  csrfToken = token;
  sessionEpoch += 1;
  resourceRevision += 1;
  resourceCache.clear();
  pendingInference.clear();
  for (const controller of activeRequests) controller.abort();
  activeRequests.clear();
  announceActivity();
}

export async function apiFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const epoch = sessionEpoch;
  const method = (options.method ?? "GET").toUpperCase();
  if (options.signal?.aborted)
    throw new DOMException("The request was cancelled.", "AbortError");
  const inference =
    method === "POST"
      ? path.match(
          /^\/api\/specifications\/([a-f0-9]{16})\/(analysis|matching)$/,
        )
      : null;
  if (
    inference &&
    [...pendingInference.values()].some(
      (item) => item.document_id === inference[1],
    )
  )
    throw new ApiError(
      "An analysis is already running for this document. No additional model call was started.",
      409,
    );
  const controller = new AbortController();
  const abort = () => controller.abort();
  // Route cleanup may stop a GET. An admitted AI POST stays connected while
  // this browser is open; changing the authenticated session still aborts it.
  const routeSignal = inference ? undefined : options.signal;
  routeSignal?.addEventListener("abort", abort, { once: true });
  activeRequests.add(controller);
  const activity = inference
    ? {
        operation_id: `client-${epoch}-${inference[1]}-${inference[2]}`,
        document_id: inference[1],
        kind:
          inference[2] === "analysis"
            ? ("extraction" as const)
            : ("matching" as const),
        started_at: new Date().toISOString(),
        elapsed_seconds: 0,
        state: "running" as const,
      }
    : null;
  if (activity) {
    pendingInference.set(activity.operation_id, activity);
    announceActivity();
  }
  const headers = new Headers(options.headers);
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken)
    headers.set("X-CSRF-Token", csrfToken);
  try {
    const response = await fetch(path, {
      ...options,
      headers,
      signal: controller.signal,
      credentials: "same-origin",
    });
    if (epoch !== sessionEpoch)
      throw new DOMException("The active workspace changed.", "AbortError");
    if (response.ok && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      resourceCache.clear();
      resourceRevision += 1;
    }
    if (
      response.status === 401 &&
      !["/api/auth/login", "/api/auth/session"].includes(path)
    )
      window.dispatchEvent(new Event("scione:session-expired"));
    if (response.status === 403) {
      const body = await response
        .clone()
        .json()
        .catch(() => ({}));
      if (
        /csrf|session changed/i.test(
          typeof body.detail === "string" ? body.detail : "",
        )
      )
        window.dispatchEvent(new Event("scione:session-changed"));
    }
    return response;
  } finally {
    activeRequests.delete(controller);
    routeSignal?.removeEventListener("abort", abort);
    if (activity) {
      pendingInference.delete(activity.operation_id);
      if (epoch === sessionEpoch) {
        inferenceCompleted(activity.document_id);
        announceActivity();
      }
    }
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const epoch = sessionEpoch;
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  const response = await apiFetch(path, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (epoch !== sessionEpoch)
      throw new DOMException("The active workspace changed.", "AbortError");
    const detail = body.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail &&
            typeof detail === "object" &&
            typeof detail.message === "string"
          ? detail.message
          : Array.isArray(detail)
            ? detail
                .map(
                  (item: { loc: string[]; msg: string }) =>
                    `${item.loc.slice(1).join(" · ")}: ${item.msg}`,
                )
                .join("; ")
            : "The request could not be completed. Please try again.";
    throw new ApiError(message, response.status);
  }
  const value = (await response.json()) as T;
  if (epoch !== sessionEpoch)
    throw new DOMException("The active workspace changed.", "AbortError");
  return value;
}

export function useResource<T>(path: string | null) {
  const [data, setData] = useState<T | null>(() => cachedResource<T>(path));
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(path));
  const [revision, setRevision] = useState(0);
  const previousPath = useRef(path);
  const pendingRefresh = useRef<{
    promise: Promise<boolean>;
    resolve: (loaded: boolean) => void;
  } | null>(null);
  const refresh = useCallback(() => {
    // Completion events and the initiating control can ask for the same read.
    // Share its completion instead of aborting/restarting it for each caller.
    if (pendingRefresh.current) return pendingRefresh.current.promise;
    let resolve!: (loaded: boolean) => void;
    const promise = new Promise<boolean>((done) => {
      resolve = done;
    });
    pendingRefresh.current = { promise, resolve };
    setRevision((value) => value + 1);
    return promise;
  }, []);
  const finishRefresh = useCallback((loaded: boolean) => {
    const pending = pendingRefresh.current;
    pendingRefresh.current = null;
    pending?.resolve(loaded);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const epoch = sessionEpoch;
    const generation = resourceRevision;
    if (previousPath.current !== path) setData(cachedResource<T>(path));
    previousPath.current = path;
    setError("");
    setLoading(Boolean(path));
    if (path)
      request<T>(path, { signal: controller.signal })
        .then((value) => {
          if (controller.signal.aborted || epoch !== sessionEpoch) return;
          if (generation !== resourceRevision) {
            // A write invalidated this read. Keep waiters attached until its
            // replacement has loaded the current saved result.
            setRevision((value) => value + 1);
            return;
          }
          rememberResource(path, value);
          setData(value);
          finishRefresh(true);
        })
        .catch((reason) => {
          if (!controller.signal.aborted) {
            setError(reason.message);
            finishRefresh(false);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    else finishRefresh(false);
    return () => controller.abort();
  }, [path, revision, finishRefresh]);
  useEffect(() => () => finishRefresh(false), [path, finishRefresh]);
  useEffect(() => {
    window.addEventListener("scione:inference-completed", refresh);
    return () =>
      window.removeEventListener("scione:inference-completed", refresh);
  }, [refresh]);
  return {
    data: previousPath.current === path ? data : cachedResource<T>(path),
    error,
    loading,
    refresh,
    setData,
  };
}
