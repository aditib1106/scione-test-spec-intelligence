import { useEffect, useMemo, useState } from "react";
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
  TextField,
  ToggleButton,
  ToggleButtonGroup,
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
const privateTest = (id: string) => id.startsWith("labtest_");
const privateMethod = (id: string) => id.startsWith("labmethod_");
function VisibilityBadge({
  isPrivate,
  compact = false,
}: {
  isPrivate: boolean;
  compact?: boolean;
}) {
  return (
    <Chip
      size="small"
      label={
        isPrivate
          ? compact
            ? "Private"
            : "Private to this lab"
          : "Shared reference"
      }
      title={isPrivate ? "Visible only within your lab" : undefined}
      sx={{
        bgcolor: isPrivate ? "#f2ecfb" : "#eaf0fc",
        color: isPrivate ? "#68449a" : "#315c9f",
        fontWeight: 600,
        height: 21,
        fontSize: 10.5,
        "& .MuiChip-label": { px: 0.9 },
      }}
    />
  );
}

export default function TestCatalog({ workspace }: PageProps) {
  const { catalog } = workspace;
  const [query, setQuery] = useState("");
  const [view, setView] = useState("all");
  const [catalogView, setCatalogView] = useState<"tests" | "methods">("tests");
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedMethodId, setSelectedMethodId] = useState<string | null>(null);
  useEffect(() => {
    setSelectedId(null);
    setSelectedMethodId(null);
  }, [workspace.lab.id, catalog.id, catalog.version]);
  const methodById = useMemo(
    () => new Map(catalog.methods.map((method) => [method.id, method])),
    [catalog.methods],
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return catalog.tests.filter((test) => {
      if (view === "shared" && privateTest(test.id)) return false;
      if (
        view === "private" &&
        !privateTest(test.id) &&
        !test.method_ids.some(privateMethod)
      )
        return false;
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
  const testsByMethod = useMemo(() => {
    const related = new Map<string, typeof catalog.tests>();
    for (const test of catalog.tests)
      for (const methodId of test.method_ids)
        related.set(methodId, [...(related.get(methodId) ?? []), test]);
    return related;
  }, [catalog.tests]);
  const filteredMethods = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return catalog.methods.filter((method) => {
      if (view === "shared" && privateMethod(method.id)) return false;
      if (view === "private" && !privateMethod(method.id)) return false;
      return [
        method.identifier,
        method.name,
        method.edition ?? "",
        ...method.aliases.map((alias) => alias.text),
        ...(testsByMethod.get(method.id) ?? []).map((test) => test.name),
      ]
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle);
    });
  }, [catalog.methods, testsByMethod, query, view]);
  const filteredCount =
    catalogView === "methods" ? filteredMethods.length : filtered.length;
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(filteredCount / rowsPerPage) - 1),
  );
  const visible = filtered.slice(
    currentPage * rowsPerPage,
    (currentPage + 1) * rowsPerPage,
  );
  const selected = catalog.tests.find((test) => test.id === selectedId);
  const selectedMethod = methodById.get(selectedMethodId ?? "");
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
        <Stack gap={1} alignItems={{ xs: "flex-start", sm: "flex-end" }}>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={catalogView}
            aria-label="Catalog view"
            onChange={(_, next: "tests" | "methods" | null) => {
              if (!next) return;
              setCatalogView(next);
              setPage(0);
              setSelectedId(null);
              setSelectedMethodId(null);
            }}
          >
            <ToggleButton value="tests" sx={{ px: 2 }}>
              Tests
            </ToggleButton>
            <ToggleButton value="methods" sx={{ px: 2 }}>
              Methods
            </ToggleButton>
          </ToggleButtonGroup>
          <Typography variant="caption" color="text.secondary">
            {catalog.tests.length} test types · {catalog.methods.length} methods
          </Typography>
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
          variant="scrollable"
          scrollButtons="auto"
          allowScrollButtonsMobile
          sx={{
            px: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
            minHeight: 48,
          }}
        >
          <Tab
            value="all"
            label={catalogView === "methods" ? "All methods" : "All tests"}
          />
          <Tab value="shared" label="Shared reference" />
          <Tab value="private" label="Private to this lab" />
        </Tabs>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          alignItems={{ sm: "center" }}
          justifyContent="space-between"
          spacing={1.5}
          sx={{ p: 2 }}
        >
          <TextField
            placeholder={
              catalogView === "methods"
                ? "Search methods, aliases or linked tests"
                : "Search tests, aliases or method codes"
            }
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
            {filteredCount}{" "}
            {catalogView === "methods"
              ? filteredCount === 1
                ? "method"
                : "methods"
              : filteredCount === 1
                ? "test"
                : "tests"}
          </Typography>
        </Stack>
        <TableContainer>
          <Table
            size="small"
            aria-label={
              catalogView === "methods" ? "Method catalog" : "Test catalog"
            }
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
              {catalogView === "methods" ? (
                <TableRow>
                  <TableCell sx={{ width: "38%" }}>Method</TableCell>
                  <TableCell sx={{ width: "12%" }}>Edition</TableCell>
                  <TableCell sx={{ width: "30%" }}>
                    Related test items
                  </TableCell>
                  <TableCell>Visibility</TableCell>
                </TableRow>
              ) : (
                <TableRow>
                  <TableCell sx={{ width: "30%" }}>Test item</TableCell>
                  <TableCell sx={{ width: "43%" }}>Test methods</TableCell>
                  <TableCell>Also known as</TableCell>
                </TableRow>
              )}
            </TableHead>
            <TableBody>
              {catalogView === "methods" &&
                filteredMethods
                  .slice(
                    currentPage * rowsPerPage,
                    (currentPage + 1) * rowsPerPage,
                  )
                  .map((method) => {
                    const related = testsByMethod.get(method.id) ?? [];
                    return (
                      <TableRow
                        key={method.id}
                        hover
                        onClick={() => setSelectedMethodId(method.id)}
                        sx={{
                          cursor: "pointer",
                          "& td": { py: 1.5, verticalAlign: "top" },
                        }}
                      >
                        <TableCell>
                          <Button
                            onClick={(event) => {
                              event.stopPropagation();
                              setSelectedMethodId(method.id);
                            }}
                            sx={{
                              p: 0,
                              minWidth: 0,
                              textAlign: "left",
                              justifyContent: "flex-start",
                              overflowWrap: "anywhere",
                            }}
                          >
                            {method.identifier || method.name}
                          </Button>
                          {method.identifier &&
                            method.name !== method.identifier && (
                              <Typography
                                variant="body2"
                                color="text.secondary"
                                sx={previewSx}
                              >
                                {method.name}
                              </Typography>
                            )}
                        </TableCell>
                        <TableCell>
                          {method.edition || "Not recorded"}
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" sx={previewSx}>
                            {related.map((test) => test.name).join(" · ") ||
                              "Not linked"}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <VisibilityBadge
                            isPrivate={privateMethod(method.id)}
                            compact
                          />
                        </TableCell>
                      </TableRow>
                    );
                  })}
              {catalogView === "tests" &&
                visible.map((test) => {
                  const methods = test.method_ids
                    .map((id) => methodById.get(id))
                    .filter(Boolean)
                    .sort((left, right) =>
                      view === "private"
                        ? Number(privateMethod(right!.id)) -
                          Number(privateMethod(left!.id))
                        : 0,
                    );
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
                        <Stack alignItems="flex-start" spacing={0.75}>
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
                          {privateTest(test.id) && (
                            <VisibilityBadge isPrivate compact />
                          )}
                        </Stack>
                      </TableCell>
                      <TableCell>
                        <Stack gap={0.5}>
                          {methods.slice(0, 3).map((method) => (
                            <Stack
                              key={method!.id}
                              direction="row"
                              gap={0.75}
                              alignItems="center"
                              flexWrap="wrap"
                            >
                              <Typography
                                variant="body2"
                                sx={{ overflowWrap: "anywhere" }}
                              >
                                {method!.identifier || method!.name}
                              </Typography>
                              {privateMethod(method!.id) &&
                                !privateTest(test.id) && (
                                  <VisibilityBadge isPrivate compact />
                                )}
                            </Stack>
                          ))}
                          {methods.length > 3 && (
                            <Typography
                              variant="caption"
                              color="primary.main"
                              sx={{ fontWeight: 600 }}
                            >
                              +{methods.length - 3} more
                            </Typography>
                          )}
                          {!methods.length && (
                            <Typography variant="body2" color="text.secondary">
                              No method recorded
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
              {!filteredCount && (
                <TableRow>
                  <TableCell
                    colSpan={catalogView === "methods" ? 4 : 3}
                    sx={{ py: 5, textAlign: "center", color: "text.secondary" }}
                  >
                    {catalogView === "methods"
                      ? "No methods match this search."
                      : "No tests match this search."}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
        <TablePagination
          component="div"
          count={filteredCount}
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
        {catalogView === "methods"
          ? "Select a method for its recorded details and related tests."
          : "Select a test for all methods and alternative names."}{" "}
        Manage your lab's supported tests in Lab capability.
      </Typography>
      <Dialog
        open={Boolean(selected)}
        onClose={() => setSelectedId(null)}
        aria-labelledby="catalog-test-title"
        maxWidth="md"
        fullWidth
      >
        <DialogTitle id="catalog-test-heading" sx={{ pb: 1 }}>
          <Typography variant="overline" color="text.secondary">
            Test reference
          </Typography>
          <Typography variant="h6" component="div" id="catalog-test-title">
            {selected?.name}
          </Typography>
        </DialogTitle>
        <DialogContent dividers>
          {selected && (
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              flexWrap="wrap"
              sx={{ mb: 2 }}
            >
              <VisibilityBadge isPrivate={privateTest(selected.id)} />
              <Typography variant="body2" color="text.secondary">
                {privateTest(selected.id)
                  ? "This test is visible only within your lab."
                  : "A shared reference test. Private methods are visible only within your lab."}
              </Typography>
            </Stack>
          )}
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
                        {method!.identifier || method!.name}
                      </Typography>
                      <Box sx={{ mt: 0.75 }}>
                        <VisibilityBadge
                          isPrivate={privateMethod(method!.id)}
                        />
                      </Box>
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
                      No method is recorded for this test.
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
      <Dialog
        open={Boolean(selectedMethod)}
        onClose={() => setSelectedMethodId(null)}
        aria-labelledby="catalog-method-title"
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle id="catalog-method-heading" sx={{ pb: 1 }}>
          <Typography variant="overline" color="text.secondary">
            Method reference
          </Typography>
          <Typography variant="h6" component="div" id="catalog-method-title">
            {selectedMethod?.identifier || selectedMethod?.name}
          </Typography>
        </DialogTitle>
        <DialogContent dividers>
          {selectedMethod && (
            <Stack gap={2.5}>
              <Box>
                <VisibilityBadge isPrivate={privateMethod(selectedMethod.id)} />
                <Typography variant="body2" sx={{ mt: 1 }}>
                  {selectedMethod.name}
                </Typography>
              </Box>
              <Box>
                <Typography variant="subtitle2">Edition</Typography>
                <Typography variant="body2" color="text.secondary">
                  {selectedMethod.edition || "Not recorded"}
                </Typography>
              </Box>
              <Box>
                <Typography variant="subtitle2">Also known as</Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {[
                    ...new Set(
                      selectedMethod.aliases.map((alias) => alias.text),
                    ),
                  ].join(" · ") || "No aliases recorded."}
                </Typography>
              </Box>
              <Box component="section" aria-label="Related test items">
                <Typography variant="subtitle2">Related test items</Typography>
                <Stack gap={0.5}>
                  {(testsByMethod.get(selectedMethod.id) ?? []).map((test) => (
                    <Typography variant="body2" key={test.id}>
                      {test.name}
                    </Typography>
                  ))}
                  {!testsByMethod.get(selectedMethod.id)?.length && (
                    <Typography variant="body2" color="text.secondary">
                      Not linked
                    </Typography>
                  )}
                </Stack>
              </Box>
              <Box>
                <Typography variant="subtitle2">
                  Procedure and source file
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Not recorded in this catalog.
                </Typography>
              </Box>
              <Typography variant="caption" color="text.secondary">
                A reference identifies a method. It does not establish lab
                support or make different methods interchangeable.
              </Typography>
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelectedMethodId(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
