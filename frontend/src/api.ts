import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message =
      typeof detail === "string"
        ? detail
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
  return { data, error, loading, refresh };
}
