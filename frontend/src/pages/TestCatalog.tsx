import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  InputAdornment,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  Tab,
  Tabs,
  Tooltip,
  TextField,
  Typography,
} from "@mui/material";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import type { PageProps } from "../types";

const previewSx = {
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
} as const;

export default function TestCatalog({ workspace }: PageProps) {
  const { catalog } = workspace;
  const [query, setQuery] = useState("");
  const [view, setView] = useState("all");
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const methodById = useMemo(
    () => new Map(catalog.methods.map((method) => [method.id, method])),
    [catalog.methods],
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return catalog.tests.filter((test) => {
      if (view === "method" && !test.method_ids.length) return false;
      if (view === "custom" && test.method_ids.length) return false;
      const methods = test.method_ids
        .map((id) => methodById.get(id))
        .filter(Boolean);
      return [
        test.name,
        ...test.aliases.map((alias) => alias.text),
        ...methods.flatMap((method) => [
          method!.name,
          method!.identifier,
          method!.edition ?? "",
          ...method!.aliases.map((alias) => alias.text),
        ]),
      ]
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle);
    });
  }, [catalog.tests, methodById, query, view]);
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(filtered.length / rowsPerPage) - 1),
  );
  const visible = filtered.slice(
    currentPage * rowsPerPage,
    (currentPage + 1) * rowsPerPage,
  );
  const selected = catalog.tests.find((test) => test.id === selectedId);
  const selectedMethods =
    selected?.method_ids.map((id) => methodById.get(id)).filter(Boolean) ?? [];

  return (
    <Box sx={{ maxWidth: 1440, mx: "auto", px: { xs: 2, md: 4 }, py: 3 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        spacing={2}
        sx={{ mb: 2.5 }}
      >
        <Box>
          <Typography variant="h4" sx={{ fontSize: 28, fontWeight: 700 }}>
            Test catalog
          </Typography>
          <Typography color="text.secondary" variant="body2" sx={{ mt: 0.75 }}>
            Browse test names, methods and alternative names.
          </Typography>
        </Box>
        <Stack direction="row" gap={1} alignItems="center">
          <Chip
            size="small"
            label={`${catalog.tests.length} test types`}
            sx={{ bgcolor: "#eaf0fc", color: "#315c9f", fontWeight: 600 }}
          />
          <Chip
            size="small"
            variant="outlined"
            label={`${catalog.methods.length} methods`}
          />
          {catalog.synthetic && (
            <Tooltip title="The catalog uses project reference data. Your lab's own declarations determine what you can offer.">
              <Chip
                size="small"
                label="Project reference"
                sx={{ color: "text.secondary", bgcolor: "#f0f3f7" }}
              />
            </Tooltip>
          )}
        </Stack>
      </Stack>
      <Paper variant="outlined" sx={{ overflow: "hidden", borderRadius: 3 }}>
        <Tabs
          value={view}
          onChange={(_, next) => {
            setView(next);
            setPage(0);
          }}
          aria-label="Catalog categories"
          sx={{
            px: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
            minHeight: 48,
          }}
        >
          <Tab value="all" label="All tests" />
          <Tab value="method" label="Method references" />
          <Tab value="custom" label="Customer-defined procedures" />
        </Tabs>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          alignItems={{ sm: "center" }}
          justifyContent="space-between"
          spacing={1.5}
          sx={{ p: 2 }}
        >
          <TextField
            placeholder="Search tests, aliases or method codes"
            inputProps={{ "aria-label": "Search test catalog" }}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
            size="small"
            sx={{ width: { xs: "100%", sm: 440 } }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchRoundedIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
          <Typography variant="body2" color="text.secondary">
            {filtered.length} {filtered.length === 1 ? "test" : "tests"}
          </Typography>
        </Stack>
        <TableContainer>
          <Table
            size="small"
            aria-label="Shared test catalog"
            sx={{
              minWidth: 720,
              tableLayout: "fixed",
              "& th": {
                bgcolor: "#f6f8fb",
                color: "#637487",
                fontSize: 12,
                py: 1.5,
              },
              "& td": { borderColor: "#edf0f4" },
            }}
          >
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: "30%" }}>Test</TableCell>
                <TableCell sx={{ width: "43%" }}>Methods</TableCell>
                <TableCell>Also known as</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visible.map((test) => {
                const methods = test.method_ids
                  .map((id) => methodById.get(id))
                  .filter(Boolean);
                const aliases = [
                  ...new Set(test.aliases.map((alias) => alias.text)),
                ].filter((text) => text !== test.name);
                return (
                  <TableRow
                    key={test.id}
                    hover
                    onClick={() => setSelectedId(test.id)}
                    sx={{
                      cursor: "pointer",
                      "& td": { py: 2, verticalAlign: "top" },
                    }}
                  >
                    <TableCell>
                      <Button
                        onClick={(event) => {
                          event.stopPropagation();
                          setSelectedId(test.id);
                        }}
                        sx={{
                          p: 0,
                          minWidth: 0,
                          textAlign: "left",
                          justifyContent: "flex-start",
                        }}
                      >
                        {test.name}
                      </Button>
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" gap={0.75} flexWrap="wrap">
                        {methods.slice(0, 3).map((method) => (
                          <Chip
                            key={method!.id}
                            size="small"
                            label={method!.identifier}
                            sx={{
                              bgcolor: "#f0f4fa",
                              color: "#3b526f",
                              fontSize: 12,
                              borderRadius: 1,
                              height: 25,
                            }}
                          />
                        ))}
                        {methods.length > 3 && (
                          <Typography
                            variant="caption"
                            color="primary.main"
                            sx={{ alignSelf: "center", fontWeight: 600 }}
                          >
                            +{methods.length - 3} more
                          </Typography>
                        )}
                        {!methods.length && (
                          <Typography variant="body2" color="text.secondary">
                            Customer-defined procedure
                          </Typography>
                        )}
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={previewSx}
                      >
                        {aliases.join(" · ") || "—"}
                      </Typography>
                    </TableCell>
                  </TableRow>
                );
              })}
              {!filtered.length && (
                <TableRow>
                  <TableCell
                    colSpan={3}
                    sx={{ py: 5, textAlign: "center", color: "text.secondary" }}
                  >
                    No tests match this search.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
        <TablePagination
          component="div"
          count={filtered.length}
          page={currentPage}
          onPageChange={(_, next) => setPage(next)}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={(event) => {
            setRowsPerPage(Number(event.target.value));
            setPage(0);
          }}
          rowsPerPageOptions={[10, 25, 50]}
          labelRowsPerPage="Rows"
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            ".MuiTablePagination-toolbar": { flexWrap: "wrap", pl: 2 },
          }}
        />
      </Paper>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 1.25 }}
      >
        Select a test for all methods and alternative names. Manage your lab's
        supported tests in My Lab.
      </Typography>
      <Dialog
        open={Boolean(selected)}
        onClose={() => setSelectedId(null)}
        aria-labelledby="catalog-test-title"
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="overline" color="text.secondary">
            Test reference
          </Typography>
          <Typography variant="h6" component="div" id="catalog-test-title">
            {selected?.name}
          </Typography>
        </DialogTitle>
        <DialogContent dividers>
          {selected?.description && (
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
              {selected.description}
            </Typography>
          )}
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Also known as
          </Typography>
          <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mb: 3 }}>
            {selected?.aliases.map((alias, index) => (
              <Chip
                key={`${alias.text}-${index}`}
                size="small"
                variant="outlined"
                label={alias.text}
              />
            ))}
            {!selected?.aliases.length && (
              <Typography color="text.secondary">
                No aliases recorded.
              </Typography>
            )}
          </Stack>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Associated methods
          </Typography>
          <TableContainer>
            <Table
              size="small"
              aria-label="Selected test methods"
              sx={{ minWidth: 500 }}
            >
              <TableHead>
                <TableRow>
                  <TableCell>Method reference</TableCell>
                  <TableCell>Method name and aliases</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {selectedMethods.map((method) => (
                  <TableRow key={method!.id}>
                    <TableCell sx={{ verticalAlign: "top", minWidth: 165 }}>
                      <Typography variant="body2" fontWeight={600}>
                        {method!.identifier}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{method!.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {[
                          ...new Set(
                            method!.aliases.map((alias) => alias.text),
                          ),
                        ]
                          .filter((text) => text !== method!.identifier)
                          .join(" · ")}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
                {!selectedMethods.length && (
                  <TableRow>
                    <TableCell colSpan={2}>
                      This is a customer-defined procedure. Its method is
                      supplied in the customer specification.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mt: 2 }}
          >
            These references identify methods; they do not establish a lab's
            capability or make different methods interchangeable.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelectedId(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
