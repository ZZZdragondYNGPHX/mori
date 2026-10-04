"""Headless launcher, also safe to call from Codex lifecycle hooks."""
from companion.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
