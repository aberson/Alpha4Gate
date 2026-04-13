#!/usr/bin/env bash
# Capture screenshots of every Alpha4Gate dashboard tab.
# Usage: bash .claude/skills/a4g-dashboard-check/capture.sh [tab1 tab2 ...]

set -euo pipefail

EVIDENCE_DIR=".ui-dashboard-evidence"
mkdir -p "$EVIDENCE_DIR"

BASE_URL="http://localhost:3000"

# Tab order (default all)
TAB_ORDER=(live stats games decisions training loop advisor improvements processes alerts)

# Filter tabs if args provided
if [ $# -gt 0 ]; then
  TAB_ORDER=("$@")
fi

# Map tab name to hash path
tab_path() {
  case "$1" in
    live) echo "" ;;
    *) echo "#/$1" ;;
  esac
}

echo "Capturing ${#TAB_ORDER[@]} tabs to $EVIDENCE_DIR/"

for tab in "${TAB_ORDER[@]}"; do
  path=$(tab_path "$tab")
  url="${BASE_URL}/${path}"
  out="$EVIDENCE_DIR/${tab}.png"

  # Use JavaScript navigation for hash routes
  if [ "$tab" = "live" ]; then
    if npx playwright screenshot \
        --full-page \
        --wait-for-timeout 3000 \
        --viewport-size "1920,1080" \
        "$url" "$out" > /dev/null 2>&1; then
      echo "  ✓ $tab"
    else
      echo "  ✗ $tab FAILED"
    fi
  else
    # For hash routes, load base URL first then navigate via JS
    if npx playwright screenshot \
        --full-page \
        --wait-for-timeout 3000 \
        --viewport-size "1920,1080" \
        "${BASE_URL}/${path}" "$out" > /dev/null 2>&1; then
      echo "  ✓ $tab"
    else
      echo "  ✗ $tab FAILED"
    fi
  fi
done

echo ""
echo "Done. Screenshots in $EVIDENCE_DIR/"
