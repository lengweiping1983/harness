#!/bin/sh
# 支持从 Finder 或任意工作目录打开独立听写应用。
set -eu
cd -- "$(dirname -- "$0")/.."
exec "${HARNESS_PYTHON:-.venv/bin/python}" -X utf8 -m harness.voice "$@"
