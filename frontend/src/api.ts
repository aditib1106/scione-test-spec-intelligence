import { useCallback, useEffect, useRef, useState } from "react";
import type { DocumentNotesReview } from "./documentNotesTypes";

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

export interface InferenceStepProgress {
  stage:
    | "planning"
    | "inventory"
    | "extracting"
    | "matching"
    | "validating"
    | "saving";
  completed_steps: number;
  total_steps: number | null;
  percent: number | null;
  message: string;
}

export interface InferenceActivity {
  operation_id: string;
  document_id: string;
  kind: "extraction" | "matching";
  started_at: string;
  elapsed_seconds: number;
  state: "running";
  progress?: InferenceStepProgress | null;
  connection_notice?: string;
  observation_state?: "stale";
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
  trackInference = true,
): Promise<Response> {
  const epoch = sessionEpoch;
  const method = (options.method ?? "GET").toUpperCase();
  if (options.signal?.aborted)
    throw new DOMException("The request was cancelled.", "AbortError");
  const inference =
    trackInference && method === "POST"
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
      signal:
        !trackInference && options.signal
          ? AbortSignal.any([controller.signal, options.signal])
          : controller.signal,
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
  const method = (options.method ?? "GET").toUpperCase();
  if (method === "GET" || method === "HEAD")
    return inferenceRequest<T>(
      path,
      options,
      epoch,
      options.signal ?? undefined,
    );
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  const response = await apiFetch(path, { ...options, headers });
  return readResponse<T>(response, epoch);
}

export function reviewDocumentNotes(
  documentId: string,
  body: {
    source_fingerprint: string;
    expected_version: number;
    note_ids: string[];
    reviewed: boolean;
  },
) {
  return request<DocumentNotesReview>(
    `/api/specifications/${encodeURIComponent(documentId)}/document-notes/review`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

async function readResponse<T>(response: Response, epoch: number): Promise<T> {
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

interface InferenceJob {
  operation_id: string;
  request_id?: string | null;
  document_id: string;
  kind: "extraction" | "matching";
  state: "running" | "completed" | "failed";
  started_at?: string;
  progress?: InferenceStepProgress | null;
  result?: { run_id: string; status: "completed"; cached: boolean };
  result_url?: string;
  error?: { status: number; detail: string | { message?: string } };
}

export class InferenceCompletionUnknownError extends Error {
  constructor() {
    super(
      "The connection was interrupted and completion could not be confirmed. No new analysis was started. Check the saved result before trying again.",
    );
  }
}

/** A bounded status read, including the response body. Never starts model work. */
export async function requestOperation<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  return inferenceRequest<T>(path, {}, sessionEpoch, signal);
}

async function inferenceRequest<T>(
  path: string,
  options: RequestInit,
  epoch: number,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timer = window.setTimeout(abort, 20_000);
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  activeRequests.add(controller);
  try {
    const headers = new Headers(options.headers);
    if (!(options.body instanceof FormData) && !headers.has("Content-Type"))
      headers.set("Content-Type", "application/json");
    const response = await apiFetch(
      path,
      { ...options, headers, signal: controller.signal },
      false,
    );
    return await readResponse<T>(response, epoch);
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
    activeRequests.delete(controller);
  }
}

/** One admission POST, then short authenticated reads until saved completion. */
export async function requestInference<T>(
  path: string,
  options: RequestInit,
): Promise<T> {
  const match = path.match(
    /^\/api\/specifications\/([a-f0-9]{16})\/(analysis|matching)$/,
  );
  if (!match || (options.method ?? "GET").toUpperCase() !== "POST")
    throw new Error(
      "An inference request requires its document admission endpoint.",
    );
  if (options.signal?.aborted)
    throw new DOMException("The request was cancelled.", "AbortError");
  const documentId = match[1];
  if (
    [...pendingInference.values()].some(
      (item) => item.document_id === documentId,
    )
  )
    throw new ApiError(
      "An analysis is already running for this document.",
      409,
    );
  const epoch = sessionEpoch;
  const requestId = crypto.randomUUID();
  const localId = `client-${requestId}`;
  const kind = match[2] === "analysis" ? "extraction" : "matching";
  // Component unmount/navigation stops its own rendering, not admitted work.
  // The session epoch still aborts all observation on logout or lab changes.
  const lifetime = new AbortController();
  activeRequests.add(lifetime);
  const activity: InferenceActivity = {
    operation_id: localId,
    document_id: documentId,
    kind,
    started_at: new Date().toISOString(),
    elapsed_seconds: 0,
    state: "running",
  };
  pendingInference.set(localId, activity);
  announceActivity();
  const assertSession = () => {
    if (epoch !== sessionEpoch || lifetime.signal.aborted)
      throw new DOMException("The active workspace changed.", "AbortError");
  };
  const pause = (milliseconds: number) =>
    new Promise<void>((resolve, reject) => {
      assertSession();
      const aborted = () => {
        window.clearTimeout(timer);
        reject(new DOMException("The active workspace changed.", "AbortError"));
      };
      const timer = window.setTimeout(() => {
        lifetime.signal.removeEventListener("abort", aborted);
        resolve();
      }, milliseconds);
      lifetime.signal.addEventListener("abort", aborted, { once: true });
    });
  const read = <R>(url: string) =>
    inferenceRequest<R>(url, {}, epoch, lifetime.signal);
  const reconnecting = (value: boolean) => {
    assertSession();
    activity.connection_notice = value
      ? "Reconnecting to check your saved result. No additional AI request is being sent."
      : undefined;
    announceActivity();
  };
  const validJob = (value: unknown): value is InferenceJob => {
    if (!value || typeof value !== "object") return false;
    const job = value as InferenceJob;
    return (
      job.document_id === documentId &&
      job.kind === kind &&
      typeof job.operation_id === "string" &&
      ["running", "completed", "failed"].includes(job.state)
    );
  };
  const recoverJob = async () => {
    const snapshot = await read<{
      recent_job?: InferenceJob | null;
      active?: InferenceJob[];
    }>(`/api/specifications/${documentId}/operations`);
    return [snapshot.recent_job, ...(snapshot.active ?? [])].find(
      (job): job is InferenceJob =>
        validJob(job) && job.request_id === requestId,
    );
  };
  try {
    let job: InferenceJob | undefined;
    try {
      const headers = new Headers(options.headers);
      headers.set("Prefer", "respond-async");
      headers.set("X-Inference-Request-ID", requestId);
      const result = await inferenceRequest<unknown>(
        path,
        { ...options, headers },
        epoch,
        lifetime.signal,
      );
      if (validJob(result)) {
        job = result;
        // An idempotent receipt may expose terminal state without its result;
        // read the authoritative status instead of treating the receipt as data.
        if (
          (job.state === "completed" && !job.result) ||
          (job.state === "failed" && !job.error)
        )
          job = { ...job, state: "running" };
      }
      // Older servers/mocked transports may still return synchronous success.
      else if (
        result &&
        typeof result === "object" &&
        ((kind === "extraction" &&
          (result as { document_id?: string }).document_id === documentId) ||
          (kind === "matching" &&
            (result as { status?: string }).status === "completed"))
      )
        return result as T;
      else throw new InferenceCompletionUnknownError();
    } catch (reason) {
      assertSession();
      if (reason instanceof ApiError && reason.status < 500) throw reason;
      reconnecting(true);
    }
    if (!job) {
      for (let attempt = 0; attempt < 3 && !job; attempt++) {
        try {
          job = await recoverJob();
        } catch {
          assertSession();
        }
        if (!job && attempt < 2) await pause(2000 * (attempt + 1));
      }
      if (!job) {
        throw new InferenceCompletionUnknownError();
      }
    }
    let failures = 0;
    let observationLostAt: number | null = null;
    const observationLost = () => {
      observationLostAt ??= Date.now();
      failures += 1;
      if (failures >= 6 || Date.now() - observationLostAt >= 90_000)
        throw new InferenceCompletionUnknownError();
    };
    for (;;) {
      assertSession();
      if (job.state === "failed") {
        const detail = job.error?.detail;
        throw new ApiError(
          typeof detail === "string"
            ? detail
            : (detail?.message ??
                "The analysis could not be completed. Your previous saved result is unchanged."),
          job.error?.status ?? 500,
        );
      }
      if (job.state === "completed") {
        if (!job.result || job.result.status !== "completed")
          throw new InferenceCompletionUnknownError();
        if (kind === "matching") return job.result as T;
        try {
          const saved = await read<{ run?: { run_id?: string } | null }>(
            `/api/specifications/${documentId}`,
          );
          if (saved.run?.run_id === job.result.run_id) return saved as T;
          throw new InferenceCompletionUnknownError();
        } catch (reason) {
          assertSession();
          if (reason instanceof InferenceCompletionUnknownError) throw reason;
          reconnecting(true);
          observationLost();
          await pause(Math.min(10_000, 2000 * failures));
          continue;
        }
      }
      activity.operation_id = job.operation_id;
      activity.progress = job.progress;
      activity.started_at = job.started_at ?? activity.started_at;
      announceActivity();
      await pause(2000);
      try {
        const current: unknown = await read<unknown>(
          `/api/operations/${encodeURIComponent(job.operation_id)}`,
        );
        if (!validJob(current) || current.operation_id !== job.operation_id)
          throw new InferenceCompletionUnknownError();
        job = current;
        reconnecting(false);
        failures = 0;
        observationLostAt = null;
      } catch (reason) {
        assertSession();
        reconnecting(true);
        try {
          const recovered = await recoverJob();
          if (recovered) {
            job = recovered;
            reconnecting(false);
            failures = 0;
            observationLostAt = null;
            continue;
          }
          if (
            (reason instanceof ApiError && reason.status === 404) ||
            reason instanceof InferenceCompletionUnknownError
          )
            throw new InferenceCompletionUnknownError();
        } catch (recoveryError) {
          assertSession();
          if (recoveryError instanceof InferenceCompletionUnknownError)
            throw recoveryError;
        }
        observationLost();
        await pause(Math.min(10_000, 2000 * failures));
      }
    }
  } finally {
    activeRequests.delete(lifetime);
    pendingInference.delete(localId);
    if (epoch === sessionEpoch) {
      inferenceCompleted(documentId);
      announceActivity();
    }
  }
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
