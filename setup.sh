#!/usr/bin/env bash
# Creates the virtual environment and installs the dependencies.
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo
echo "Setup complete! Start the app with ./run.sh"
