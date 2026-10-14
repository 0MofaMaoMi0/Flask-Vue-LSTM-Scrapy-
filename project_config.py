"""Project-local environment loading shared by Flask and Scrapy.

The loader intentionally does not override values already supplied by the
process, so deployment environment variables take precedence over `.env`.
"""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"


def load_project_env():
    """Load simple KEY=VALUE entries from the project-local `.env` file."""
    if not ENV_FILE.is_file():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def required(name):
    """Return a required setting without ever exposing its value in errors."""
    load_project_env()
    value = os.getenv(name)
    if not value:
        raise RuntimeError("Missing required configuration: %s" % name)
    return value


def optional(name, default=None):
    """Return an optional setting after loading the project-local `.env`."""
    load_project_env()
    return os.getenv(name, default)
