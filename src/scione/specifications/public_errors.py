"""Short lab-facing errors; full attempt diagnostics remain in private storage."""


def public_analysis_error(message: str, error_code: str | None = None) -> str:
    if error_code == "input_too_large" or (
        "input characters including" in message and "configured character guard" in message
    ):
        return (
            "This document is too long for the current analysis limit. "
            "Upload smaller sections, including their shared test conditions. "
            "No AI request was sent; the original PDF and earlier results are kept."
        )
    return message
