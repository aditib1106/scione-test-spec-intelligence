import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  InputAdornment,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ScienceOutlinedIcon from "@mui/icons-material/ScienceOutlined";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import VisibilityOffOutlinedIcon from "@mui/icons-material/VisibilityOffOutlined";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import { useAuth } from "../auth";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Box
      sx={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        p: 2,
        bgcolor: "#f4f7fc",
      }}
    >
      <Paper
        component="form"
        elevation={0}
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || !username.trim() || !password) return;
          setBusy(true);
          setError("");
          try {
            await login(username.trim(), password);
            navigate("/specifications/library", { replace: true });
          } catch (reason) {
            setError(
              reason instanceof Error
                ? reason.message
                : "Unable to sign in. Try again.",
            );
          } finally {
            setBusy(false);
          }
        }}
        sx={{
          width: "100%",
          maxWidth: 440,
          px: { xs: 3, sm: 4.5 },
          py: 4.5,
          border: "1px solid #e0e7f1",
          borderRadius: 3,
          boxShadow: "0 12px 48px rgba(31,58,103,.06)",
        }}
      >
        <Stack direction="row" alignItems="center" gap={1.25} sx={{ mb: 4 }}>
          <Box
            sx={{
              display: "grid",
              placeItems: "center",
              width: 42,
              height: 44,
              color: "#fff",
              bgcolor: "primary.main",
              borderRadius: "12px 12px 12px 4px",
            }}
          >
            <ScienceOutlinedIcon />
          </Box>
          <Box>
            <Typography fontSize={24} fontWeight={750} letterSpacing="-.06em">
              SciOne
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Lab intelligence
            </Typography>
          </Box>
        </Stack>
        <Typography
          component="h1"
          fontSize={26}
          fontWeight={750}
          sx={{ mb: 1 }}
        >
          Welcome back
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          lineHeight={1.65}
          sx={{ mb: 3 }}
        >
          Sign in to review customer specifications and your lab’s testing
          capabilities.
        </Typography>
        <Stack gap={2}>
          {params.get("password") === "updated" && (
            <Alert severity="success">
              Password updated. Sign in with your new password.
            </Alert>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="Username"
            name="username"
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            disabled={busy}
            fullWidth
          />
          <TextField
            label="Password"
            name="password"
            autoComplete="current-password"
            required
            type={showPassword ? "text" : "password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={busy}
            fullWidth
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton
                    aria-label={
                      showPassword ? "Hide password" : "Show password"
                    }
                    onClick={() => setShowPassword((value) => !value)}
                    edge="end"
                  >
                    <>
                      {showPassword ? (
                        <VisibilityOffOutlinedIcon />
                      ) : (
                        <VisibilityOutlinedIcon />
                      )}
                    </>
                  </IconButton>
                </InputAdornment>
              ),
            }}
          />
          <Button
            type="submit"
            variant="contained"
            size="large"
            disabled={busy || !username.trim() || !password}
            endIcon={
              busy ? (
                <CircularProgress size={18} color="inherit" />
              ) : (
                <ArrowForwardRoundedIcon />
              )
            }
            sx={{ mt: 0.5, minHeight: 48 }}
          >
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </Stack>
        <Typography
          variant="caption"
          color="text.secondary"
          display="block"
          textAlign="center"
          sx={{ mt: 3.5 }}
        >
          Need access? Contact your SciOne administrator.
        </Typography>
      </Paper>
    </Box>
  );
}
