import { useMemo, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Card,
  Chip,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import MenuBookOutlinedIcon from "@mui/icons-material/MenuBookOutlined";
import type { PageProps } from "../types";

export default function TestCatalog({ workspace }: PageProps) {
  const { catalog } = workspace;
  const [query, setQuery] = useState("");
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
      const text = [
        test.name,
        test.description,
        test.id,
        ...test.aliases.map((alias) => `${alias.text} ${alias.language}`),
        ...methods.flatMap((method) => [
          method!.name,
          method!.identifier,
          method!.edition ?? "",
          method!.id,
          ...method!.aliases.map((alias) => `${alias.text} ${alias.language}`),
        ]),
      ]
        .join(" ")
        .toLocaleLowerCase();
      return text.includes(needle);
    });
  }, [catalog.tests, methodById, query]);

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
        <Box>
          <Typography variant="overline" color="primary.main">
            REFERENCE
          </Typography>
          <Typography variant="h4" fontWeight={700}>
            Test catalog
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Browse canonical test identities and the methods associated with
            each one.
          </Typography>
        </Box>
        <TextField
          size="small"
          placeholder="Search tests, aliases, methods"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          sx={{ width: { xs: "100%", sm: 310 } }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchRoundedIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />
      </Stack>
      {catalog.synthetic && (
        <Alert icon={<MenuBookOutlinedIcon />} severity="info" sx={{ mb: 2 }}>
          This reference catalog contains synthetic demonstration metadata.
          Confirm identities and method associations against an authoritative
          source before operational use. Associated methods are not
          automatically equivalent; standard procedures are not included.
        </Alert>
      )}
      <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
        <Chip
          size="small"
          variant="outlined"
          label={`${filtered.length} of ${catalog.tests.length} tests`}
        />
        <Chip
          size="small"
          variant="outlined"
          label={`${catalog.methods.length} methods`}
        />
      </Stack>
      <Card
        variant="outlined"
        sx={{ borderColor: "divider", overflow: "hidden" }}
      >
        {filtered.length === 0 ? (
          <Box sx={{ p: 4, textAlign: "center" }}>
            <Typography fontWeight={600}>No catalog entries match</Typography>
            <Typography variant="body2" color="text.secondary">
              Search by a test name, alias, method code or method title.
            </Typography>
          </Box>
        ) : (
          filtered.map((test, index) => {
            const methods = test.method_ids
              .map((id) => methodById.get(id))
              .filter(Boolean);
            return (
              <Accordion
                key={test.id}
                disableGutters
                elevation={0}
                square
                sx={{
                  borderBottom: index < filtered.length - 1 ? "1px solid" : 0,
                  borderColor: "divider",
                  "&:before": { display: "none" },
                }}
              >
                <AccordionSummary
                  expandIcon={<ExpandMoreRoundedIcon />}
                  sx={{
                    px: 2,
                    py: 0.5,
                    "& .MuiAccordionSummary-content": { my: 1.25 },
                  }}
                >
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    justifyContent="space-between"
                    spacing={0.5}
                    sx={{ width: "100%", pr: 1 }}
                  >
                    <Box sx={{ minWidth: 0 }}>
                      <Typography fontWeight={650}>{test.name}</Typography>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ mt: 0.25 }}
                      >
                        {test.description}
                      </Typography>
                    </Box>
                    <Stack
                      direction="row"
                      spacing={0.75}
                      alignItems={{ sm: "center" }}
                      sx={{ flexShrink: 0 }}
                    >
                      <Chip
                        size="small"
                        label={`${methods.length} ${methods.length === 1 ? "method" : "methods"}`}
                      />
                      <Typography variant="caption" color="text.secondary">
                        {test.id}
                      </Typography>
                    </Stack>
                  </Stack>
                </AccordionSummary>
                <AccordionDetails sx={{ px: 2, pb: 2.25, pt: 0 }}>
                  {test.aliases.length > 0 && (
                    <Box sx={{ mb: 2 }}>
                      <Typography
                        variant="caption"
                        fontWeight={700}
                        color="text.secondary"
                      >
                        TEST ALIASES
                      </Typography>
                      <Stack
                        direction="row"
                        flexWrap="wrap"
                        gap={0.5}
                        sx={{ mt: 0.75 }}
                      >
                        {test.aliases.map((alias, i) => (
                          <Chip
                            key={`${alias.language}-${i}`}
                            size="small"
                            variant="outlined"
                            label={`${alias.text} · ${alias.language}`}
                          />
                        ))}
                      </Stack>
                    </Box>
                  )}
                  <Typography
                    variant="caption"
                    fontWeight={700}
                    color="text.secondary"
                  >
                    ASSOCIATED METHODS
                  </Typography>
                  {methods.length ? (
                    <Stack spacing={0.75} sx={{ mt: 0.75 }}>
                      {methods.map((method) => (
                        <Box
                          key={method!.id}
                          sx={{
                            px: 1.25,
                            py: 1,
                            bgcolor: "action.hover",
                            borderRadius: 1.25,
                          }}
                        >
                          <Stack
                            direction={{ xs: "column", sm: "row" }}
                            justifyContent="space-between"
                            spacing={0.5}
                          >
                            <Box>
                              <Typography variant="body2" fontWeight={650}>
                                {method!.identifier}
                                {method!.edition ? ` · ${method!.edition}` : ""}
                              </Typography>
                              <Typography
                                variant="body2"
                                color="text.secondary"
                              >
                                {method!.name}
                              </Typography>
                            </Box>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ alignSelf: { sm: "center" } }}
                            >
                              ID: {method!.id}
                            </Typography>
                          </Stack>
                          {method!.aliases.length > 0 && (
                            <Stack
                              direction="row"
                              flexWrap="wrap"
                              gap={0.5}
                              sx={{ mt: 0.75 }}
                            >
                              {method!.aliases.map((alias, i) => (
                                <Chip
                                  key={`${alias.language}-${i}`}
                                  size="small"
                                  label={`${alias.text} · ${alias.language}`}
                                />
                              ))}
                            </Stack>
                          )}
                        </Box>
                      ))}
                    </Stack>
                  ) : (
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ mt: 0.75 }}
                    >
                      No methods are associated with this test.
                    </Typography>
                  )}
                </AccordionDetails>
              </Accordion>
            );
          })
        )}
      </Card>
    </Box>
  );
}
