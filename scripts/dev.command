#!/bin/sh
# 从任意目录或 Finder 启动现有前后端热更新入口。
set -eu
cd -- "$(dirname -- "$0")/.."
exec "${HARNESS_PYTHON:-.venv/bin/python}" -X utf8 scripts/dev.py "$@"
