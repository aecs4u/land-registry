#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-aecs4u/land-registry}"
BASE_DIR="docs/github_issues/zone_architecture"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh auth is not valid. Run: gh auth login -h github.com"
  exit 1
fi

create_issue() {
  local title="$1"
  local body_file="$2"
  echo "Creating: $title"
  gh issue create --repo "$REPO" --title "$title" --body-file "$body_file"
}

create_issue "Architecture: Unify app shell and task-first sidebar IA" "$BASE_DIR/01-unify-app-shell-and-sidebar-ia.md"
create_issue "Data Model: Introduce Zone and Microzone entities with migrations" "$BASE_DIR/02-zone-microzone-domain-model-and-migrations.md"
create_issue "API: Zone CRUD management endpoints" "$BASE_DIR/03-zone-crud-api.md"
create_issue "API: Microzone CRUD endpoints nested under zones" "$BASE_DIR/04-microzone-crud-api.md"
create_issue "UI: Zone panel CRUD, collapse behavior, and live zone count" "$BASE_DIR/05-zone-panel-ui-crud.md"
create_issue "UI: Microzone controls with area badges and large-area warnings" "$BASE_DIR/06-microzone-ui-area-warning.md"
create_issue "UI: Bulk selection and zone search workflows" "$BASE_DIR/07-bulk-selection-and-search-ui.md"
create_issue "Spatial APIs: Point lookup and zone overlay (within/intersects)" "$BASE_DIR/08-spatial-lookup-apis-point-and-zone.md"
create_issue "Map: Draw-all-microzones rendering and keyboard navigation" "$BASE_DIR/09-map-rendering-and-keyboard-navigation.md"
create_issue "Navigation: Zone-level Sales and Rents deep links" "$BASE_DIR/10-zone-driven-sales-rents-navigation.md"

echo "Done."
