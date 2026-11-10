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

/** Clear every in-flight request before accepting another user's/lab's session. */
export function configureApiSession(token: string | null) {
  csrfToken = token;
  sessionEpoch += 1;
  for (const controller of activeRequests) controller.abort();
  activeRequests.clear();
}

export async function apiFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const epoch = sessionEpoch;
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  activeRequests.add(controller);
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
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
    options.signal?.removeEventListener("abort", abort);
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  const response = await apiFetch(path, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
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
  return response.json() as Promise<T>;
}

export function useResource<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(path));
  const [revision, setRevision] = useState(0);
  const previousPath = useRef(path);
  const refresh = useCallback(() => {
    setRevision((value) => value + 1);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    if (previousPath.current !== path) setData(null);
    previousPath.current = path;
    setError("");
    setLoading(Boolean(path));
    if (path)
      request<T>(path, { signal: controller.signal })
        .then((value) => {
          if (!controller.signal.aborted) setData(value);
        })
        .catch((reason) => {
          if (!controller.signal.aborted) setError(reason.message);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    return () => controller.abort();
  }, [path, revision]);
  return { data, error, loading, refresh, setData };
}
