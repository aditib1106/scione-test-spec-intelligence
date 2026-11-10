import { useState } from "react";
import {
  Alert,
  Avatar,
  Box,
  Button,
  CircularProgress,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import LogoutRoundedIcon from "@mui/icons-material/LogoutRounded";
import PasswordRoundedIcon from "@mui/icons-material/PasswordRounded";
import { useAuth } from "../auth";
import { useResource } from "../api";

export default function SessionControls() {
  const auth = useAuth();
  const user = auth.session?.user;
  const enabled = auth.session?.enabled !== false;
  const labs = useResource<{ labs: { lab_id: string; lab_name: string }[] }>(
    enabled && auth.canDevelop ? "/api/auth/workspaces" : null,
  );
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const closePassword = () => {
    if (busy) return;
    setPasswordOpen(false);
    setCurrentPassword("");
    setNewPassword("");
    setConfirmation("");
    setPasswordError("");
  };
  if (!user) return null;
  return (
    <Stack direction="row" gap={1.25} alignItems="center" sx={{ minWidth: 0 }}>
      {auth.canDevelop && enabled && labs.data?.labs.length ? (
        <TextField
          select
          size="small"
          label="Viewing lab"
          value={user.lab_id ?? ""}
          disabled={busy}
          onChange={async (event) => {
            if (event.target.value === user.lab_id) return;
            setBusy(true);
            setError("");
            try {
              await auth.switchLab(event.target.value);
            } catch (reason) {
              setError(
                reason instanceof Error
                  ? reason.message
                  : "Could not switch lab.",
              );
              setBusy(false);
            }
          }}
          sx={{
            width: { xs: 150, sm: 220 },
            "& .MuiInputBase-root": { fontSize: 12, height: 34 },
          }}
        >
          {!user.lab_id && (
            <MenuItem value="" disabled>
              Select a lab
            </MenuItem>
          )}
          {labs.data.labs.map((lab) => (
            <MenuItem key={lab.lab_id} value={lab.lab_id}>
              {lab.lab_name}
            </MenuItem>
          ))}
        </TextField>
      ) : null}
      {auth.canDevelop && enabled && (
        <Chip
          label="Platform administrator"
          size="small"
          sx={{
            display: { xs: "none", md: "flex" },
            bgcolor: "#eef2fb",
            color: "#3c567f",
            fontSize: 11,
          }}
        />
      )}
      {enabled ? (
        <>
          <Tooltip title={`Account · ${user.display_name || user.username}`}>
            <IconButton
              aria-label="Account menu"
              onClick={(event) => setAnchor(event.currentTarget)}
              size="small"
              sx={{ p: 0.25 }}
            >
              <Avatar
                sx={{
                  width: 29,
                  height: 29,
                  bgcolor: "#eaf0fb",
                  color: "#2d58a1",
                  fontSize: 12,
                  fontWeight: 700,
                }}
              >
                {(user.display_name || user.username).slice(0, 1).toUpperCase()}
              </Avatar>
            </IconButton>
          </Tooltip>
          <Menu
            anchorEl={anchor}
            open={Boolean(anchor)}
            onClose={() => setAnchor(null)}
          >
            <Box sx={{ px: 2, pt: 1, pb: 1.5, minWidth: 190 }}>
              <Typography variant="body2" fontWeight={650}>
                {auth.canDevelop
                  ? "Platform administrator"
                  : user.display_name || user.username}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {auth.canDevelop
                  ? user.username
                  : user.lab_name || "Lab workspace"}
              </Typography>
              {auth.canDevelop && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  display="block"
                >
                  Viewing lab: {user.lab_name || "not selected"}
                </Typography>
              )}
            </Box>
            <MenuItem
              disabled={busy}
              onClick={() => {
                setAnchor(null);
                setPasswordError("");
                setPasswordOpen(true);
              }}
            >
              <PasswordRoundedIcon fontSize="small" sx={{ mr: 1 }} />
              Change password
            </MenuItem>
            <MenuItem
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  await auth.logout();
                } catch (reason) {
                  setError(
                    reason instanceof Error
                      ? reason.message
                      : "Could not sign out. Try again.",
                  );
                  setBusy(false);
                }
              }}
            >
              <LogoutRoundedIcon fontSize="small" sx={{ mr: 1 }} />
              Sign out
            </MenuItem>
            {error && (
              <Alert severity="error" sx={{ m: 1, maxWidth: 300 }}>
                {error}
              </Alert>
            )}
          </Menu>
          <Dialog
            open={passwordOpen}
            onClose={closePassword}
            maxWidth="sm"
            fullWidth
          >
            <Box
              component="form"
              onSubmit={async (event) => {
                event.preventDefault();
                if (busy) return;
                setPasswordError("");
                if (newPassword.length < 15 || newPassword.length > 1024) {
                  setPasswordError(
                    "Use a new password between 15 and 1,024 characters.",
                  );
                  return;
                }
                if (newPassword !== confirmation) {
                  setPasswordError("New passwords do not match.");
                  return;
                }
                setBusy(true);
                try {
                  await auth.changePassword(currentPassword, newPassword);
                } catch (reason) {
                  setPasswordError(
                    reason instanceof Error
                      ? reason.message
                      : "Could not update your password. Try again.",
                  );
                  setBusy(false);
                }
              }}
            >
              <DialogTitle>Change password</DialogTitle>
              <DialogContent>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mb: 2 }}
                >
                  You'll be signed out on all devices after saving.
                </Typography>
                <Stack gap={2}>
                  {passwordError && (
                    <Alert severity="error">{passwordError}</Alert>
                  )}
                  <TextField
                    label="Current password"
                    type="password"
                    autoComplete="current-password"
                    required
                    fullWidth
                    value={currentPassword}
                    disabled={busy}
                    onChange={(event) => setCurrentPassword(event.target.value)}
                    inputProps={{ maxLength: 1024 }}
                  />
                  <TextField
                    label="New password"
                    type="password"
                    autoComplete="new-password"
                    required
                    fullWidth
                    value={newPassword}
                    disabled={busy}
                    helperText="Use 15 to 1,024 characters."
                    onChange={(event) => setNewPassword(event.target.value)}
                    inputProps={{ maxLength: 1024 }}
                  />
                  <TextField
                    label="Confirm new password"
                    type="password"
                    autoComplete="new-password"
                    required
                    fullWidth
                    value={confirmation}
                    disabled={busy}
                    onChange={(event) => setConfirmation(event.target.value)}
                    inputProps={{ maxLength: 1024 }}
                  />
                </Stack>
              </DialogContent>
              <DialogActions sx={{ px: 3, pb: 2.5 }}>
                <Button onClick={closePassword} disabled={busy}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="contained"
                  disabled={
                    busy || !currentPassword || !newPassword || !confirmation
                  }
                  startIcon={busy ? <CircularProgress size={16} /> : undefined}
                >
                  {busy ? "Saving…" : "Update password"}
                </Button>
              </DialogActions>
            </Box>
          </Dialog>
        </>
      ) : (
        <Typography variant="caption" color="text.secondary">
          Platform administrator · local
        </Typography>
      )}
      {error && !anchor && (
        <Alert
          severity="error"
          sx={{
            position: "absolute",
            top: 64,
            right: 16,
            zIndex: 1600,
            maxWidth: 340,
          }}
        >
          {error}
        </Alert>
      )}
    </Stack>
  );
}
