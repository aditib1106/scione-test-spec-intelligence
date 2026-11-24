import { Button, Tooltip } from "@mui/material";
import FindInPageOutlinedIcon from "@mui/icons-material/FindInPageOutlined";

/** Compact visible reference; the full action remains available to assistive tools. */
export default function SourcePageReference({
  page,
  label,
  onClick,
  compact = false,
}: {
  page: number;
  label: string;
  onClick: () => void;
  compact?: boolean;
}) {
  return (
    <Tooltip title={`Source · Page ${page}`}>
      <Button
        size="small"
        aria-label={label}
        onClick={onClick}
        startIcon={
          <FindInPageOutlinedIcon sx={{ fontSize: "16px !important" }} />
        }
        sx={{
          minWidth: 0,
          ...(compact ? { minHeight: 24 } : {}),
          px: 0.65,
          py: 0.25,
          gap: 0.1,
          fontSize: 11,
          lineHeight: 1.5,
          borderRadius: 1,
          color: "#4166a3",
          "& .MuiButton-startIcon": { mr: 0.35 },
          "&:hover": { bgcolor: "#eaf1fb" },
        }}
      >
        {page}
      </Button>
    </Tooltip>
  );
}
