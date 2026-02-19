## Summary
The app currently exposes two UI shells (`/` and `/map`) with different capabilities and confusing navigation. We need a single app shell and a task-first sidebar IA.

## Problem
- Post-login route points users to a shell that does not expose the full zone workflow.
- Sidebar groups controls by technical implementation, not by user jobs.
- Users must switch context across both sidebar and top view tabs.

## Scope
- Pick one canonical shell route for map operations.
- Ensure auth redirects land on that shell.
- Redesign sidebar information architecture to task-first groups:
  - Data
  - Explore
  - Analyze
  - Zones
  - Actions (Sales/Rents)
  - Account

## Acceptance Criteria
- Only one primary map shell is used in production.
- Zone and microzone workflows are available in the canonical shell.
- Sidebar labels and grouping match user jobs and documented workflows.
- Legacy route either redirects or is clearly deprecated.

## Dependencies
- None
