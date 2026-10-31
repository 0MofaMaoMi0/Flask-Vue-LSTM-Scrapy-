#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$SCRIPT_DIR/../.venv-scrapy/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/../.venv-scrapy/bin/python"
else
    PYTHON="$SCRIPT_DIR/../.venv-scrapy/Scripts/python.exe"
fi

cd "$SCRIPT_DIR"
exec "$PYTHON" -m scrapy crawl movie_comment "$@"
