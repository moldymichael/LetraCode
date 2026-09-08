"""Frozen GUI entry point, deliberately independent of the current directory."""
import multiprocessing
import os
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()
    # A Windows GUI process does not have a console. Libraries may still write
    # diagnostics; provide real file objects just as pythonw-compatible apps do.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    from letracode.app import main
    raise SystemExit(main())
