#!/usr/bin/env bash
# Setups the repository.

# Stop on errors
set -e

cd "$(dirname "$0")/.."

pip3 install poetry
poetry config virtualenvs.in-project true
poetry install
poetry run pre-commit install
