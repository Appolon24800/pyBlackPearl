#!/usr/bin/env bash
# Builds a native executable with PyInstaller (see build.spec).
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "Virtual environment not found. Run ./setup.sh first." >&2
    exit 1
fi

./venv/bin/pyinstaller --clean build.spec

echo
echo "Build finished: dist/"
