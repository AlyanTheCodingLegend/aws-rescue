#!/usr/bin/env bash
# Launch the Streamlit dashboard.
# Run from project root: bash scripts/run_dashboard.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/.env" 2>/dev/null || true

echo "Starting AWS-RESCUE dashboard..."
echo "Project: ${RESCUE_PROJECT_ID:-rescue42}"
echo "Open http://localhost:8501 in your browser."
echo ""

cd "$ROOT"
streamlit run dashboard/app.py \
  --server.headless false \
  --server.port 8501 \
  --browser.gatherUsageStats false
