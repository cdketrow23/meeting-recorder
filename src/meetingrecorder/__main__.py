"""``python -m meetingrecorder`` entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from .app import run

    try:
        run()
    except KeyboardInterrupt:
        return 130
    except Exception:
        # Last-resort graceful exit
        import traceback

        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
