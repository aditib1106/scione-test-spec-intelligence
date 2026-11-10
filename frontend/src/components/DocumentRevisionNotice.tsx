import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Button,
  Stack,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { Link as RouterLink } from "react-router-dom";

export default function DocumentRevisionNotice({
  issues,
  sourceReviewUrl,
}: {
  issues?: string[];
  sourceReviewUrl?: string;
}) {
  if (!issues?.length) return null;
  return (
    <Alert severity="warning" sx={{ flexShrink: 0 }}>
      <Typography variant="body2" fontWeight={650}>
        Check document revision
      </Typography>
      <Accordion
        disableGutters
        elevation={0}
        sx={{ bgcolor: "transparent", "&:before": { display: "none" } }}
      >
        <AccordionSummary
          expandIcon={<ExpandMoreIcon />}
          sx={{ p: 0, minHeight: 32 }}
        >
          <Typography variant="caption">View revision details</Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ p: 0 }}>
          <Stack gap={1}>
            {issues.map((issue, index) => (
              <Typography
                key={index}
                variant="body2"
                sx={{ overflowWrap: "anywhere" }}
              >
                {issue}
              </Typography>
            ))}
          </Stack>
          {sourceReviewUrl && (
            <Button
              component={RouterLink}
              to={sourceReviewUrl}
              size="small"
              sx={{ mt: 1, px: 0 }}
            >
              Open PDF review
            </Button>
          )}
        </AccordionDetails>
      </Accordion>
    </Alert>
  );
}
