"""PyInstaller entrypoint.

PyInstaller runs the script named in `meetingrecorder.spec` as a top-level
module, so package-relative imports inside `meetingrecorder.__main__` fail when
that file is used directly. This tiny top-level shim imports the package entry
point normally instead.
"""

from __future__ import annotations

import sys

from meetingrecorder.__main__ import main


if __name__ == "__main__":
    sys.exit(main())
