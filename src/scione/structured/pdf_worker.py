"""One isolated PDF render: UTF-8 JSON stdin, PDF bytes stdout, no source logging."""

from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        from scione.structured.pdf import _render_comparison_pdf_inline

        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return 2
        result = _render_comparison_pdf_inline(payload)
        if not result.startswith(b"%PDF-"):
            return 3
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # Do not print exceptions, source values, environment or credentials.
        # The caller supplies a controlled domain error without accepting bytes.
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
