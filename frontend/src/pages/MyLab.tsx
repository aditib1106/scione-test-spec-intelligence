import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import AddRoundedIcon from "@mui/icons-material/AddRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import ScienceOutlinedIcon from "@mui/icons-material/ScienceOutlined";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import { ApiError, request } from "../api";
import type { Capability, LabProfile, PageProps } from "../types";
import CapabilityEditor from "./CapabilityEditor";

const money = (capability: Capability) =>
  capability.price
    ? `${capability.currency ?? ""} ${capability.price}${capability.pricing_basis ? ` / ${capability.pricing_basis}` : ""}`.trim()
    : "—";

export default function MyLab({ workspace, onRefresh }: PageProps) {
  const { lab, catalog } = workspace;
  const [query, setQuery] = useState("");
  const [editingCapability, setEditingCapability] = useState<
    Capability | undefined
  >();
  const [capabilityOpen, setCapabilityOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileName, setProfileName] = useState(lab.name);
  const [profileDescription, setProfileDescription] = useState(lab.description);
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [busyExample, setBusyExample] = useState(false);
  const [exampleError, setExampleError] = useState("");
  const [exampleNotice, setExampleNotice] = useState("");
  const [saveError, setSaveError] = useState("");

  const tests = useMemo(
    () => new Map(catalog.tests.map((test) => [test.id, test])),
    [catalog.tests],
  );
  const methods = useMemo(
    () => new Map(catalog.methods.map((method) => [method.id, method])),
    [catalog.methods],
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return lab.capabilities.filter((capability) => {
      if (!needle) return true;
      const test = tests.get(capability.canonical_test_id);
      const methodText = capability.method_ids
        .map((id) => methods.get(id))
        .filter(Boolean)
        .map(
          (method) =>
            `${method!.identifier} ${method!.name} ${method!.edition ?? ""} ${method!.aliases.map((a) => a.text).join(" ")}`,
        )
        .join(" ");
      return `${test?.name ?? capability.canonical_test_id} ${test?.id ?? ""} ${test?.aliases.map((alias) => alias.text).join(" ") ?? ""} ${methodText} ${capability.declared_constraints ?? ""}`
        .toLocaleLowerCase()
        .includes(needle);
    });
  }, [lab.capabilities, methods, query, tests]);

  async function saveCapability(capability: Capability) {
    setSaveError("");
    const capabilities = editingCapability
      ? lab.capabilities.map((item) =>
          item.id === editingCapability.id ? capability : item,
        )
      : [...lab.capabilities, capability];
    const updated = await request<LabProfile>("/api/lab", {
      method: "PUT",
      body: JSON.stringify({ ...lab, capabilities }),
    });
    setCapabilityOpen(false);
    setEditingCapability(undefined);
    if (!updated) return;
    onRefresh();
  }

  async function saveProfile() {
    setSavingProfile(true);
    setProfileError("");
    try {
      await request<LabProfile>("/api/lab", {
        method: "PUT",
        body: JSON.stringify({
          ...lab,
          name: profileName.trim(),
          description: profileDescription,
        }),
      });
      setProfileOpen(false);
      onRefresh();
    } catch (reason) {
      const message =
        reason instanceof Error
          ? reason.message
          : "The lab details could not be saved.";
      setProfileError(
        reason instanceof ApiError && reason.status === 409
          ? `${message} Your draft is still here. Reload the latest lab before trying again.`
          : message,
      );
    } finally {
      setSavingProfile(false);
    }
  }

  async function loadExample() {
    setBusyExample(true);
    setExampleError("");
    setExampleNotice("");
    try {
      await request<LabProfile>("/api/lab/example", {
        method: "POST",
        body: JSON.stringify({ version: lab.version }),
      });
      setExampleNotice("Example capabilities loaded.");
      onRefresh();
    } catch (reason) {
      const message =
        reason instanceof Error
          ? reason.message
          : "Example capabilities could not be loaded.";
      setExampleError(
        reason instanceof ApiError && reason.status === 409
          ? `${message} Reload the latest lab and try again.`
          : message,
      );
    } finally {
      setBusyExample(false);
    }
  }

  const editCapability = (capability?: Capability) => {
    setEditingCapability(capability);
    setCapabilityOpen(true);
  };

  return (
    <Box
      sx={{
        maxWidth: 1200,
        mx: "auto",
        px: { xs: 2, md: 4 },
        py: { xs: 3, md: 4 },
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "stretch", sm: "center" }}
        spacing={2}
        sx={{ mb: 3 }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="overline" color="primary.main">
            LAB PROFILE
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="h4" fontWeight={700}>
              {lab.name}
            </Typography>
            <IconButton
              size="small"
              aria-label="Edit lab details"
              onClick={() => {
                setProfileName(lab.name);
                setProfileDescription(lab.description);
                setProfileError("");
                setProfileOpen(true);
              }}
            >
              <EditOutlinedIcon fontSize="small" />
            </IconButton>
          </Stack>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            {lab.description || "Your lab capabilities, methods and pricing."}
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<AddRoundedIcon />}
          onClick={() => editCapability()}
          sx={{ flexShrink: 0, minWidth: 124, whiteSpace: "nowrap" }}
        >
          Add a test
        </Button>
      </Stack>

      <Stack direction="row" spacing={{ xs: 1, sm: 1.5 }} sx={{ mb: 2.5 }}>
        {[
          ["Capabilities", lab.capabilities.length],
          [
            "Methods declared",
            new Set(lab.capabilities.flatMap((cap) => cap.method_ids)).size,
          ],
          [
            "Priced tests",
            lab.capabilities.filter((cap) => cap.price !== null).length,
          ],
        ].map(([label, value]) => (
          <Card
            key={label}
            variant="outlined"
            sx={{ flex: 1, borderColor: "divider" }}
          >
            <CardContent sx={{ py: "14px !important" }}>
              <Typography variant="caption" color="text.secondary">
                {label}
              </Typography>
              <Typography variant="h5" fontWeight={700}>
                {value}
              </Typography>
            </CardContent>
          </Card>
        ))}
      </Stack>

      {saveError && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" onClick={onRefresh}>
              Reload lab
            </Button>
          }
        >
          {saveError}
        </Alert>
      )}
      {exampleError && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" onClick={onRefresh}>
              Reload lab
            </Button>
          }
        >
          {exampleError}
        </Alert>
      )}
      {exampleNotice && (
        <Alert
          severity="success"
          sx={{ mb: 2 }}
          onClose={() => setExampleNotice("")}
        >
          {exampleNotice}
        </Alert>
      )}

      <Card
        variant="outlined"
        sx={{ borderColor: "divider", overflow: "hidden" }}
      >
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ xs: "stretch", sm: "center" }}
          spacing={1.5}
          sx={{ px: 2, py: 1.5 }}
        >
          <Box>
            <Typography variant="h6" fontWeight={650}>
              Capabilities
            </Typography>
            <Typography variant="caption" color="text.secondary">
              What your lab can perform, with declared scope and pricing.
            </Typography>
          </Box>
          <TextField
            size="small"
            placeholder="Search tests and methods"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            sx={{ width: { xs: "100%", sm: 280 } }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchRoundedIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
        </Stack>
        <Divider />
        {lab.capabilities.length === 0 ? (
          <Stack
            alignItems="center"
            textAlign="center"
            spacing={1.25}
            sx={{ px: 3, py: 6 }}
          >
            <ScienceOutlinedIcon color="disabled" sx={{ fontSize: 36 }} />
            <Typography variant="h6">
              Start with your lab’s capabilities
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ maxWidth: 480 }}
            >
              Add tests and declare the methods, operating conditions and
              pricing your lab supports.
            </Typography>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1}
              sx={{ mt: 1 }}
            >
              <Button
                variant="contained"
                startIcon={<AddRoundedIcon />}
                onClick={() => editCapability()}
              >
                Add a test
              </Button>
              <Button
                variant="outlined"
                onClick={loadExample}
                disabled={busyExample}
              >
                {busyExample ? (
                  <CircularProgress size={18} />
                ) : (
                  "Load example lab"
                )}
              </Button>
            </Stack>
          </Stack>
        ) : filtered.length === 0 ? (
          <Box sx={{ p: 4, textAlign: "center" }}>
            <Typography fontWeight={600}>No matches found</Typography>
            <Typography variant="body2" color="text.secondary">
              Try another test name or method code.
            </Typography>
          </Box>
        ) : (
          <Box>
            {filtered.map((capability, index) => {
              const test = tests.get(capability.canonical_test_id);
              const declaredMethods = capability.method_ids
                .map((id) => methods.get(id))
                .filter(Boolean);
              return (
                <Box
                  key={capability.id}
                  sx={{
                    px: 2,
                    py: 1.75,
                    borderBottom: index < filtered.length - 1 ? "1px solid" : 0,
                    borderColor: "divider",
                  }}
                >
                  <Stack
                    direction={{ xs: "column", md: "row" }}
                    justifyContent="space-between"
                    spacing={1.25}
                  >
                    <Box sx={{ minWidth: 0, flex: 1 }}>
                      <Typography fontWeight={650}>
                        {test?.name ?? capability.canonical_test_id}
                      </Typography>
                      {declaredMethods.length > 0 && (
                        <Stack
                          direction="row"
                          flexWrap="wrap"
                          gap={0.5}
                          sx={{ mt: 0.75 }}
                        >
                          {declaredMethods.map((method) => (
                            <Chip
                              key={method!.id}
                              size="small"
                              label={`${method!.identifier}${method!.edition ? ` · ${method!.edition}` : ""}`}
                            />
                          ))}
                        </Stack>
                      )}
                      <Stack
                        direction="row"
                        flexWrap="wrap"
                        gap={1}
                        sx={{ mt: 1 }}
                      >
                        <Typography variant="body2" color="text.secondary">
                          <b>Conditions</b> ·{" "}
                          {capability.declared_constraints ||
                            (capability.structured_limits.length
                              ? "Operating limits declared — view in Edit"
                              : "Not specified")}
                        </Typography>
                        {capability.notes && (
                          <Typography variant="body2" color="text.secondary">
                            <b>Notes</b> · {capability.notes}
                          </Typography>
                        )}
                      </Stack>
                    </Box>
                    <Stack
                      direction="row"
                      alignItems="center"
                      justifyContent="space-between"
                      spacing={2}
                      sx={{ minWidth: { md: 205 } }}
                    >
                      <Box sx={{ textAlign: { md: "right" } }}>
                        <Typography variant="caption" color="text.secondary">
                          Price
                        </Typography>
                        <Typography fontWeight={650}>
                          {money(capability)}
                        </Typography>
                      </Box>
                      <Button
                        size="small"
                        startIcon={<EditOutlinedIcon />}
                        onClick={() => editCapability(capability)}
                      >
                        Edit
                      </Button>
                    </Stack>
                  </Stack>
                </Box>
              );
            })}
          </Box>
        )}
      </Card>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 1.5 }}
      >
        Lab declarations and reference rates require review before use in a
        quote. Example capabilities are illustrative.
      </Typography>

      <CapabilityEditor
        onReload={() => {
          setCapabilityOpen(false);
          setEditingCapability(undefined);
          setSaveError("");
          onRefresh();
        }}
        open={capabilityOpen}
        catalog={catalog}
        initial={editingCapability}
        onClose={() => {
          setCapabilityOpen(false);
          setEditingCapability(undefined);
        }}
        onSave={async (cap) => {
          try {
            await saveCapability(cap);
          } catch (reason) {
            const message =
              reason instanceof Error ? reason.message : "Save failed.";
            setSaveError(
              reason instanceof ApiError && reason.status === 409
                ? `${message} Your changes are still in the editor.`
                : message,
            );
            throw reason;
          }
        }}
      />
      <Dialog
        open={profileOpen}
        onClose={savingProfile ? undefined : () => setProfileOpen(false)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Edit lab details</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {profileError && (
              <Alert
                severity="error"
                action={
                  profileError.includes("draft is still here") ? (
                    <Button color="inherit" size="small" onClick={onRefresh}>
                      Reload
                    </Button>
                  ) : undefined
                }
              >
                {profileError}
              </Alert>
            )}
            <TextField
              label="Lab name"
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              required
            />
            <TextField
              label="Description"
              value={profileDescription}
              onChange={(e) => setProfileDescription(e.target.value)}
              multiline
              minRows={3}
            />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button
            onClick={() => setProfileOpen(false)}
            disabled={savingProfile}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={saveProfile}
            disabled={savingProfile || !profileName.trim()}
          >
            {savingProfile ? "Saving…" : "Save details"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
