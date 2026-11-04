import { useMemo, useState } from "react";
import {
  Alert,
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
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const methodById = useMemo(
    () => new Map(catalog.methods.map((method) => [method.id, method])),
    [catalog.methods],
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return catalog.tests;
    return catalog.tests.filter((test) => {
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
  }, [catalog.tests, methodById, query]);
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
      <Stack spacing={0.5} sx={{ mb: 2.5 }}>
        <Typography variant="overline" color="primary.main">
          SHARED REFERENCE
        </Typography>
        <Typography variant="h4">Test catalog</Typography>
        <Typography color="text.secondary">
          Find test names, method references and aliases used to identify
          customer requirements.
        </Typography>
      </Stack>
      {catalog.synthetic && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Synthetic demonstration catalog. Method associations do not establish
          equivalence; standard procedures are not included.
        </Alert>
      )}
      <Paper variant="outlined" sx={{ overflow: "hidden" }}>
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
            sx={{ width: { xs: "100%", sm: 420 } }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchRoundedIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
          <Typography variant="body2" color="text.secondary">
            {filtered.length} test types · {catalog.methods.length} method
            references
          </Typography>
        </Stack>
        <TableContainer>
          <Table
            size="small"
            aria-label="Shared test catalog"
            sx={{ minWidth: 760, tableLayout: "fixed" }}
          >
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: "28%" }}>Test</TableCell>
                <TableCell sx={{ width: "37%" }}>Methods</TableCell>
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
                      "& td": { py: 1.5, verticalAlign: "top" },
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
                      <Typography variant="body2" sx={previewSx}>
                        {methods
                          .map((method) => method!.identifier)
                          .join(" · ") || "No method listed"}
                      </Typography>
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
        Select a test to view all associated methods and aliases. Your lab's own
        supported methods and limits are maintained in My Lab.
      </Typography>
      <Dialog
        open={Boolean(selected)}
        onClose={() => setSelectedId(null)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>{selected?.name}</DialogTitle>
        <DialogContent dividers>
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
                      No associated method recorded.
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
