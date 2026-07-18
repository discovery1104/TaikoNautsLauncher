"""Minimal frozen entry point; application code lives in launcher_core.pyd."""

from launcher_core import main


if __name__ == "__main__":
    raise SystemExit(main())
