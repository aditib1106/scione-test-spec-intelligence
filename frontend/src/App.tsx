import { lazy, Suspense, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  Typography,
} from "@mui/material";
import ScienceOutlined from "@mui/icons-material/ScienceOutlined";
import GridViewOutlined from "@mui/icons-material/GridViewOutlined";
import DescriptionOutlined from "@mui/icons-material/DescriptionOutlined";
import AssessmentOutlined from "@mui/icons-material/AssessmentOutlined";
import CodeRounded from "@mui/icons-material/CodeRounded";
import MenuRounded from "@mui/icons-material/MenuRounded";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import { useResource } from "./api";
import type { Workspace } from "./types";
const MyLab = lazy(() => import("./pages/MyLab"));
const TestCatalog = lazy(() => import("./pages/TestCatalog"));
const Specifications = lazy(() => import("./pages/Specifications"));
const Reports = lazy(() => import("./pages/Reports"));
const Developer = lazy(() => import("./pages/Developer"));

const navigation = [
  { path: "/my-lab", label: "My Lab", icon: ScienceOutlined },
  { path: "/catalog", label: "Test Catalog", icon: GridViewOutlined },
  {
    path: "/specifications",
    label: "Specifications",
    icon: DescriptionOutlined,
  },
  { path: "/reports", label: "Reports", icon: AssessmentOutlined },
];

export default function App() {
  const {
    data: workspace,
    error,
    refresh,
  } = useResource<Workspace>("/api/workspace");
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const active =
    navigation.find((item) => location.pathname === item.path)?.label ??
    (location.pathname === "/developer" ? "Developer Tools" : "My Lab");
  const sidebar = (
    <Stack sx={{ height: "100%", px: 2, py: 3 }}>
      <Stack
        direction="row"
        gap={1.3}
        alignItems="center"
        sx={{ px: 1, mb: 4 }}
      >
        <Box
          sx={{
            bgcolor: "primary.main",
            color: "#fff",
            width: 35,
            height: 39,
            borderRadius: "10px 10px 10px 3px",
            display: "grid",
            placeItems: "center",
          }}
        >
          <ScienceOutlined fontSize="small" />
        </Box>
        <Box>
          <Typography
            sx={{
              fontSize: 21,
              fontWeight: 700,
              letterSpacing: "-.055em",
              lineHeight: 1.15,
            }}
          >
            SciOne
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Lab intelligence
          </Typography>
        </Box>
      </Stack>
      <Typography
        variant="overline"
        color="text.secondary"
        sx={{ px: 1.5, mb: 1 }}
      >
        Workspace
      </Typography>
      <List disablePadding aria-label="Workspace navigation">
        {navigation.map(({ path, label, icon: Icon }) => (
          <ListItemButton
            key={path}
            component={NavLink}
            to={path}
            onClick={() => setMobileOpen(false)}
            sx={{
              borderRadius: 1,
              mb: 0.5,
              px: 1.5,
              minHeight: 44,
              color: "text.secondary",
              "&.active": {
                bgcolor: "#edf3ff",
                color: "primary.main",
                "& .MuiListItemIcon-root": { color: "primary.main" },
              },
            }}
          >
            <ListItemIcon sx={{ minWidth: 34, color: "#7d899a" }}>
              <Icon sx={{ fontSize: 20 }} />
            </ListItemIcon>
            <ListItemText
              primary={label}
              primaryTypographyProps={{ fontSize: 13, fontWeight: 600 }}
            />
          </ListItemButton>
        ))}
      </List>
      <Box sx={{ flex: 1 }} />
      <ListItemButton
        component={NavLink}
        to="/developer"
        onClick={() => setMobileOpen(false)}
        sx={{
          borderRadius: 1,
          minHeight: 42,
          color: "text.secondary",
          "&.active": { bgcolor: "#edf3ff", color: "primary.main" },
        }}
      >
        <ListItemIcon sx={{ minWidth: 32, color: "inherit" }}>
          <CodeRounded fontSize="small" />
        </ListItemIcon>
        <ListItemText
          primary="Developer Tools"
          primaryTypographyProps={{ fontSize: 12 }}
        />
      </ListItemButton>
      <Divider sx={{ my: 2 }} />
      <Box sx={{ px: 1.5 }}>
        <Stack direction="row" gap={0.75} alignItems="center">
          <Box
            sx={{
              width: 6,
              height: 6,
              bgcolor: "#3f8b6a",
              borderRadius: "50%",
            }}
          />
          <Typography variant="caption" fontWeight={600}>
            Local workspace
          </Typography>
        </Stack>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5 }}
        >
          Your lab data stays on this device.
        </Typography>
      </Box>
    </Stack>
  );

  return (
    <Box sx={{ display: "flex", minHeight: "100dvh" }}>
      <Box
        component="a"
        href="#main"
        sx={{
          position: "fixed",
          top: -80,
          left: 16,
          p: 1.5,
          bgcolor: "white",
          zIndex: 1600,
          "&:focus": { top: 8 },
        }}
      >
        Skip to content
      </Box>
      <Drawer
        variant="permanent"
        sx={{
          width: 232,
          flexShrink: 0,
          display: { xs: "none", lg: "block" },
          "& .MuiDrawer-paper": {
            width: 232,
            boxSizing: "border-box",
            borderRightColor: "divider",
          },
        }}
      >
        {sidebar}
      </Drawer>
      <Drawer
        variant="temporary"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        sx={{ display: { lg: "none" }, "& .MuiDrawer-paper": { width: 260 } }}
      >
        {sidebar}
      </Drawer>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack
          component="header"
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{
            height: 64,
            px: { xs: 2, md: 4 },
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: "rgba(255,255,255,.96)",
            position: "sticky",
            top: 0,
            zIndex: 1100,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <IconButton
              aria-label="Open navigation"
              onClick={() => setMobileOpen(true)}
              sx={{ display: { lg: "none" } }}
            >
              <MenuRounded />
            </IconButton>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ display: { xs: "none", sm: "block" } }}
            >
              Workspace{" "}
              <Box component="span" sx={{ px: 1.3, color: "#c3cbd8" }}>
                /
              </Box>
            </Typography>
            <Typography variant="body2" fontWeight={600}>
              {active}
            </Typography>
          </Stack>
          <Chip
            size="small"
            variant="outlined"
            label="Engineer review"
            sx={{ color: "text.secondary", fontSize: 11 }}
          />
        </Stack>
        <Box component="main" id="main" tabIndex={-1} sx={{ outline: "none" }}>
          {error && (
            <Alert
              severity="error"
              action={
                <Button onClick={refresh} color="inherit">
                  Retry
                </Button>
              }
              sx={{ m: 3 }}
            >
              {error}
            </Alert>
          )}
          {!workspace && !error ? (
            <Stack
              alignItems="center"
              justifyContent="center"
              sx={{ minHeight: "60vh" }}
            >
              <CircularProgress size={28} />
              <Typography color="text.secondary" sx={{ mt: 2 }}>
                Opening your workspace…
              </Typography>
            </Stack>
          ) : (
            workspace && (
              <Suspense
                fallback={
                  <Box sx={{ p: 4 }}>
                    <CircularProgress size={24} />
                  </Box>
                }
              >
                <Routes>
                  <Route path="/" element={<Navigate to="/my-lab" replace />} />
                  <Route
                    path="/my-lab"
                    element={
                      <MyLab workspace={workspace} onRefresh={refresh} />
                    }
                  />
                  <Route
                    path="/catalog"
                    element={
                      <TestCatalog workspace={workspace} onRefresh={refresh} />
                    }
                  />
                  <Route
                    path="/specifications"
                    element={
                      <Specifications
                        workspace={workspace}
                        onRefresh={refresh}
                      />
                    }
                  />
                  <Route
                    path="/reports"
                    element={
                      <Reports workspace={workspace} onRefresh={refresh} />
                    }
                  />
                  <Route
                    path="/developer"
                    element={
                      <Developer workspace={workspace} onRefresh={refresh} />
                    }
                  />
                  <Route
                    path="*"
                    element={
                      <Stack alignItems="center" spacing={2} sx={{ py: 10 }}>
                        <Typography variant="h4">Page not found</Typography>
                        <Button
                          component={Link}
                          to="/my-lab"
                          endIcon={<ArrowForwardRounded />}
                        >
                          Return to My Lab
                        </Button>
                      </Stack>
                    }
                  />
                </Routes>
              </Suspense>
            )
          )}
        </Box>
      </Box>
    </Box>
  );
}
