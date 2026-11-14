import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { ApiError, configureApiSession, request } from "./api";

export interface SessionUser {
  id: string;
  username: string;
  display_name: string;
  role: "lab" | "maintainer";
  lab_id: string | null;
  lab_name: string | null;
}
export interface Session {
  authenticated: boolean;
  enabled?: boolean;
  user: SessionUser | null;
  csrf_token: string | null;
}
interface AuthState {
  session: Session | null;
  loading: boolean;
  error: string;
  canDevelop: boolean;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (
    currentPassword: string,
    newPassword: string,
  ) => Promise<void>;
  switchLab: (labId: string) => Promise<void>;
}
const AuthContext = createContext<AuthState | null>(null);
const sessionTabId = globalThis.crypto?.randomUUID?.() ?? String(Math.random());

function notifyOtherTabs() {
  try {
    const channel = new BroadcastChannel("scione-session");
    channel.postMessage({ event: "changed", sender: sessionTabId });
    channel.close();
  } catch {
    // Session cookies still work when cross-tab messaging is unavailable.
  }
}

export function clearWorkspacePreferences() {
  for (const name of ["sessionStorage", "localStorage"] as const) {
    try {
      const storage = window[name];
      for (let at = storage.length - 1; at >= 0; at--) {
        const key = storage.key(at);
        if (key && /^scione[.:]/.test(key)) storage.removeItem(key);
      }
    } catch {
      /* Private browsing may disable optional storage. */
    }
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const current = await request<Session>("/api/auth/session");
      configureApiSession(current.csrf_token);
      setSession(current);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError")
        return;
      setError(
        "We could not open your session. Check the connection and try again.",
      );
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    const expired = () => {
      configureApiSession(null);
      clearWorkspacePreferences();
      setSession({ authenticated: false, user: null, csrf_token: null });
      setLoading(false);
    };
    window.addEventListener("scione:session-expired", expired);
    const changed = () => {
      configureApiSession(null);
      clearWorkspacePreferences();
      setLoading(true);
      window.location.replace("/specifications/library");
    };
    window.addEventListener("scione:session-changed", changed);
    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel("scione-session");
      channel.onmessage = (event) => {
        if (
          event.data?.event === "changed" &&
          event.data.sender !== sessionTabId
        )
          changed();
      };
    } catch {
      // A stale write is still rejected by the server's rotating CSRF token.
    }
    return () => {
      window.removeEventListener("scione:session-expired", expired);
      window.removeEventListener("scione:session-changed", changed);
      channel?.close();
    };
  }, []);
  const login = async (username: string, password: string) => {
    const current = await request<Session>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    if (!current.authenticated || !current.user)
      throw new ApiError("Sign-in could not be completed.", 401);
    clearWorkspacePreferences();
    configureApiSession(current.csrf_token);
    setSession(current);
    notifyOtherTabs();
  };
  const logout = async () => {
    await request<Session>("/api/auth/logout", { method: "POST" });
    configureApiSession(null);
    clearWorkspacePreferences();
    setSession({ authenticated: false, user: null, csrf_token: null });
    notifyOtherTabs();
    window.location.replace("/login");
  };
  const switchLab = async (labId: string) => {
    setLoading(true);
    try {
      const current = await request<Session>("/api/auth/workspace", {
        method: "POST",
        body: JSON.stringify({ lab_id: labId }),
      });
      configureApiSession(current.csrf_token);
      clearWorkspacePreferences();
      setSession(current);
      notifyOtherTabs();
      // A fresh document clears browser module state and every old-lab view.
      window.location.replace("/specifications/library");
    } catch (reason) {
      setLoading(false);
      throw reason;
    }
  };
  const changePassword = async (
    currentPassword: string,
    newPassword: string,
  ) => {
    await request<Session>("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
    configureApiSession(null);
    clearWorkspacePreferences();
    setSession({ authenticated: false, user: null, csrf_token: null });
    notifyOtherTabs();
    window.location.replace("/login?password=updated");
  };
  return (
    <AuthContext.Provider
      value={{
        session,
        loading,
        error,
        refresh,
        login,
        logout,
        changePassword,
        switchLab,
        canDevelop:
          session?.authenticated === true &&
          session.user?.role === "maintainer",
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("The sign-in context is unavailable.");
  return auth;
}
