#!/usr/bin/env bash
# One-time upload of the CSV history already in git to the Databricks volume.
# Needs a logged-in Databricks CLI (DATABRICKS_HOST and DATABRICKS_TOKEN, or a profile).
set -euo pipefail

VOLUME_PATH="${DATABRICKS_VOLUME_PATH:-/Volumes/workspace/inmuebles/csv_diarios}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Only details files feed the pipeline. One recursive copy is much faster than one call per file.
find "$REPO_ROOT/data" -name '*_details_*.csv' -exec cp {} "$STAGE"/ \;
echo "Uploading $(ls "$STAGE" | wc -l | tr -d ' ') files to $VOLUME_PATH"
databricks fs cp -r "$STAGE" "dbfs:$VOLUME_PATH" --overwrite
