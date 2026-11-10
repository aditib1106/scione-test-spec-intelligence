import {
  lazy,
  Suspense,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
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
  CircularProgress,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  Tab,
  Tabs,
  Typography,
  Tooltip,
} from "@mui/material";
import ScienceOutlined from "@mui/icons-material/ScienceOutlined";
import GridViewOutlined from "@mui/icons-material/GridViewOutlined";
import DescriptionOutlined from "@mui/icons-material/DescriptionOutlined";
import AssessmentOutlined from "@mui/icons-material/AssessmentOutlined";
import CodeRounded from "@mui/icons-material/CodeRounded";
import MenuRounded from "@mui/icons-material/MenuRounded";
import MenuOpenRounded from "@mui/icons-material/MenuOpenRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import { useResource } from "./api";
import type { Workspace } from "./types";
const MyLab = lazy(() => import("./pages/MyLab"));
const TestCatalog = lazy(() => import("./pages/TestCatalog"));
const Specifications = lazy(() => import("./pages/Specifications"));
const SpecificationLibrary = lazy(() => import("./pages/SpecificationLibrary"));
const Reports = lazy(() => import("./pages/Reports"));
const Comparison = lazy(() => import("./pages/Comparison"));
const Developer = lazy(() => import("./pages/Developer"));

const navigation = [
  {
    path: "/specifications/library",
    label: "Specifications",
    icon: DescriptionOutlined,
  },
  { path: "/reports", label: "Reports", icon: AssessmentOutlined },
  { path: "/my-lab", label: "My Lab", icon: ScienceOutlined },
  { path: "/catalog", label: "Test Catalog", icon: GridViewOutlined },
];

const SIDEBAR_PREFERENCE = "scione.sidebar.collapsed";

function savedSidebarPreference() {
  try {
    return window.localStorage.getItem(SIDEBAR_PREFERENCE) === "true";
  } catch {
    return false;
  }
}

export default function App() {
  const {
    data: workspace,
    error,
    refresh,
  } = useResource<Workspace>("/api/workspace");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    savedSidebarPreference,
  );
  const pendingScroll = useRef<{ x: number; y: number } | null>(null);
  const sidebarWidth = sidebarCollapsed ? 72 : 232;

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_PREFERENCE, String(sidebarCollapsed));
    } catch {
      // Navigation remains usable when browser storage is unavailable.
    }
  }, [sidebarCollapsed]);

  useLayoutEffect(() => {
    if (pendingScroll.current) {
      const position = pendingScroll.current;
      pendingScroll.current = null;
      window.scrollTo(position.x, position.y);
    }
  }, [sidebarCollapsed]);

  const toggleSidebar = () => {
    pendingScroll.current = { x: window.scrollX, y: window.scrollY };
    setSidebarCollapsed((value) => !value);
  };
  const location = useLocation();
  const isReportWorkspace = location.pathname === "/reports";
  const active =
    navigation.find((item) =>
      item.path === "/specifications/library"
        ? location.pathname.startsWith("/specifications")
        : item.path === "/reports"
          ? location.pathname.startsWith("/reports")
          : location.pathname === item.path,
    )?.label ??
    (location.pathname === "/developer" ? "Developer Tools" : "My Lab");
  const sidebar = (compact = false, mobile = false) => (
    <Stack sx={{ height: "100%", px: compact ? 1 : 2, py: 2.5 }}>
      <Stack
        direction="row"
        gap={1.3}
        alignItems="center"
        sx={{
          px: compact ? 0 : 1,
          mb: 3,
          justifyContent: compact ? "center" : undefined,
        }}
      >
        <Tooltip
          title={compact ? "SciOne lab workspace" : ""}
          placement="right"
        >
          <Box
            sx={{
              bgcolor: "primary.main",
              color: "#fff",
              width: 35,
              height: 39,
              flexShrink: 0,
              borderRadius: "10px 10px 10px 3px",
              display: "grid",
              placeItems: "center",
            }}
          >
            <ScienceOutlined fontSize="small" />
          </Box>
        </Tooltip>
        {!compact && (
          <Box sx={{ flex: 1 }}>
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
        )}
        {mobile && (
          <IconButton
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
            size="small"
          >
            <CloseRounded fontSize="small" />
          </IconButton>
        )}
      </Stack>
      {!compact && (
        <Typography
          variant="overline"
          color="text.secondary"
          sx={{ px: 1.5, mb: 1 }}
        >
          Workspace
        </Typography>
      )}
      <List disablePadding aria-label="Workspace navigation">
        {navigation.map(({ path, label, icon: Icon }) => (
          <Tooltip
            key={path}
            title={compact ? label : ""}
            placement="right"
            disableInteractive
          >
            <ListItemButton
              component={NavLink}
              to={path}
              aria-label={label}
              onClick={() => setMobileOpen(false)}
              sx={{
                borderRadius: 1,
                mb: 0.5,
                px: compact ? 0 : 1.5,
                justifyContent: compact ? "center" : undefined,
                minHeight: 44,
                color: "text.secondary",
                ...((path === "/specifications/library" &&
                  location.pathname.startsWith("/specifications")) ||
                (path === "/reports" &&
                  location.pathname.startsWith("/reports"))
                  ? {
                      bgcolor: "#edf3ff",
                      color: "primary.main",
                      "& .MuiListItemIcon-root": { color: "primary.main" },
                    }
                  : {}),
                "&.active": {
                  bgcolor: "#edf3ff",
                  color: "primary.main",
                  "& .MuiListItemIcon-root": { color: "primary.main" },
                },
              }}
            >
              <ListItemIcon
                sx={{ minWidth: compact ? 0 : 34, color: "#7d899a" }}
              >
                <Icon sx={{ fontSize: 20 }} />
              </ListItemIcon>
              {!compact && (
                <ListItemText
                  primary={label}
                  primaryTypographyProps={{ fontSize: 13, fontWeight: 600 }}
                />
              )}
            </ListItemButton>
          </Tooltip>
        ))}
      </List>
      <Box sx={{ flex: 1 }} />
      <Tooltip
        title={compact ? "Developer Tools" : ""}
        placement="right"
        disableInteractive
      >
        <ListItemButton
          component={NavLink}
          to="/developer"
          aria-label="Developer Tools"
          onClick={() => setMobileOpen(false)}
          sx={{
            borderRadius: 1,
            minHeight: 42,
            px: compact ? 0 : 1.5,
            justifyContent: compact ? "center" : undefined,
            color: "text.secondary",
            "&.active": { bgcolor: "#edf3ff", color: "primary.main" },
          }}
        >
          <ListItemIcon sx={{ minWidth: compact ? 0 : 32, color: "inherit" }}>
            <CodeRounded fontSize="small" />
          </ListItemIcon>
          {!compact && (
            <ListItemText
              primary="Developer Tools"
              primaryTypographyProps={{ fontSize: 12 }}
            />
          )}
        </ListItemButton>
      </Tooltip>
      <Divider sx={{ my: 2 }} />
      <Tooltip title={compact ? "Local workspace" : ""} placement="right">
        <Box sx={{ px: compact ? 0 : 1.5 }}>
          <Stack
            direction="row"
            gap={0.75}
            alignItems="center"
            justifyContent={compact ? "center" : undefined}
          >
            <Box
              sx={{
                width: 6,
                height: 6,
                bgcolor: "#3f8b6a",
                borderRadius: "50%",
              }}
            />
            {!compact && (
              <Typography variant="caption" fontWeight={600}>
                Local workspace
              </Typography>
            )}
          </Stack>
          {!compact && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5 }}
            >
              Your lab data stays on this device.
            </Typography>
          )}
        </Box>
      </Tooltip>
    </Stack>
  );

  return (
    <Box
      sx={{
        display: "flex",
        minHeight: "100dvh",
        "--workspace-header-height": isReportWorkspace ? "44px" : "64px",
      }}
    >
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
        slotProps={{
          paper: { id: "desktop-navigation", "aria-label": "Main navigation" },
        }}
        sx={{
          width: sidebarWidth,
          flexShrink: 0,
          display: { xs: "none", lg: "block" },
          "& .MuiDrawer-paper": {
            width: sidebarWidth,
            boxSizing: "border-box",
            overflowX: "hidden",
            borderRightColor: "divider",
          },
        }}
      >
        {sidebar(sidebarCollapsed)}
      </Drawer>
      <Drawer
        variant="temporary"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        sx={{ display: { lg: "none" }, "& .MuiDrawer-paper": { width: 260 } }}
      >
        {sidebar(false, true)}
      </Drawer>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack
          component="header"
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{
            height: "var(--workspace-header-height)",
            px: { xs: 2, md: isReportWorkspace ? 2.5 : 4 },
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
            <Tooltip
              title={
                sidebarCollapsed ? "Expand navigation" : "Collapse navigation"
              }
            >
              <IconButton
                aria-label={
                  sidebarCollapsed ? "Expand navigation" : "Collapse navigation"
                }
                aria-expanded={!sidebarCollapsed}
                aria-controls="desktop-navigation"
                onClick={toggleSidebar}
                sx={{ display: { xs: "none", lg: "inline-flex" } }}
              >
                {sidebarCollapsed ? <MenuRounded /> : <MenuOpenRounded />}
              </IconButton>
            </Tooltip>
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
        </Stack>
        <Box component="main" id="main" tabIndex={-1} sx={{ outline: "none" }}>
          {location.pathname.startsWith("/specifications") && (
            <Box
              sx={{
                borderBottom: "1px solid",
                borderColor: "divider",
                bgcolor: "background.paper",
                px: { xs: 2, md: 4 },
              }}
            >
              <Tabs
                value={
                  location.pathname === "/specifications/library"
                    ? "library"
                    : "review"
                }
                aria-label="Specification workspace"
              >
                <Tab
                  label="Library"
                  value="library"
                  component={Link}
                  to={`/specifications/library${location.search}`}
                />
                <Tab
                  label="PDF & review"
                  value="review"
                  component={Link}
                  to={`/specifications${location.search}`}
                />
              </Tabs>
            </Box>
          )}
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
                  <Route
                    path="/"
                    element={<Navigate to="/specifications/library" replace />}
                  />
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
                    path="/specifications/library"
                    element={
                      <SpecificationLibrary
                        workspace={workspace}
                        onRefresh={refresh}
                      />
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
                      <Comparison workspace={workspace} onRefresh={refresh} />
                    }
                  />
                  <Route
                    path="/reports/advanced"
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
