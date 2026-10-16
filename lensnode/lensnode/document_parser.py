"""Isolate document text parsing so callers can stop unresponsive parsers."""

import contextlib
import json
import sys


def main():
    """Return extracted text as JSON on stdout and keep parser logs on stderr."""

    try:
        with contextlib.redirect_stdout(sys.stderr):
            from markitdown import MarkItDown

            result = MarkItDown().convert(sys.argv[1])
        text = getattr(result, "text_content", None)
        print(json.dumps({"text_content": str(result) if text is None else text}))
    except ImportError:
        print(json.dumps({"error": "MARKITDOWN_NOT_AVAILABLE"}))
        return 1
    except Exception as exc:
        print(json.dumps({"error": str(exc) or type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
