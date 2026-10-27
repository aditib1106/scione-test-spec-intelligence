import { createTheme } from "@mui/material/styles";

export const theme = createTheme({
  palette: {
    primary: { main: "#2459cc", dark: "#19449f", light: "#edf3ff" },
    background: { default: "#f5f7fb", paper: "#ffffff" },
    text: { primary: "#17263b", secondary: "#66758b" },
    divider: "#e2e8f0",
    success: { main: "#227451" },
    warning: { main: "#946018" },
    error: { main: "#ba3945" },
  },
  typography: {
    fontFamily:
      'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    fontSize: 13,
    h4: {
      fontSize: "1.875rem",
      fontWeight: 700,
      letterSpacing: "-.045em",
      lineHeight: 1.3,
    },
    h5: { fontSize: "1.5rem", fontWeight: 600, letterSpacing: "-.03em" },
    h6: { fontSize: "1.05rem", fontWeight: 600, letterSpacing: "-.02em" },
    body1: { fontSize: ".875rem", lineHeight: 1.65 },
    body2: { fontSize: ".8125rem", lineHeight: 1.65 },
    subtitle1: { fontSize: ".9375rem", fontWeight: 600 },
    overline: { fontSize: ".65rem", fontWeight: 700, letterSpacing: ".13em" },
    caption: { fontSize: ".73rem", lineHeight: 1.6 },
  },
  shape: { borderRadius: 10 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { minWidth: 320 },
        "*": { boxSizing: "border-box" },
        a: { color: "inherit" },
        "button, input, textarea": { font: "inherit" },
        "::selection": { background: "#d9e6ff" },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 600,
          borderRadius: 7,
          minHeight: 36,
          whiteSpace: "normal",
          lineHeight: 1.5,
        },
        contained: { boxShadow: "none" },
      },
    },
    MuiTextField: { defaultProps: { size: "small" } },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: { outlined: { borderColor: "#e2e8f0" } },
    },
    MuiCard: { defaultProps: { variant: "outlined" } },
    MuiChip: {
      styleOverrides: {
        root: { borderRadius: 6, fontWeight: 500 },
        sizeSmall: { height: 25, fontSize: ".7rem" },
      },
    },
    MuiAlert: {
      styleOverrides: { root: { borderRadius: 8, fontSize: ".8125rem" } },
    },
    MuiDialog: {
      defaultProps: { fullWidth: true },
      styleOverrides: { paper: { backgroundImage: "none", borderRadius: 14 } },
    },
    MuiDialogTitle: {
      styleOverrides: { root: { fontSize: "1.2rem", fontWeight: 600 } },
    },
    MuiTab: {
      styleOverrides: {
        root: { textTransform: "none", minHeight: 48, fontWeight: 600 },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        head: {
          background: "#f8fafc",
          color: "#66758b",
          fontSize: ".73rem",
          fontWeight: 600,
        },
        body: { fontSize: ".8125rem" },
      },
    },
  },
});
