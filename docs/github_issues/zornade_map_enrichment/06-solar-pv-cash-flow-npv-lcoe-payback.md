## Summary
Add an editable PV cash-flow scenario to the parcel panel, using the municipal
PVGIS-derived potential exposed by `aecs4u-stats` and financial assumptions
that the user can inspect and change. The user supplies system size; parcel
area is not a proxy for usable roof area.

**Progress (2026-10-17):** the app now has
`POST /api/v1/enrichment/solar-economics/{national_reference}` and a lazy
Energy-tab form. It returns year-one generation, annual cash flows, NPV, LCOE,
simple and discounted payback, plus data and assumption provenance. The
selected parcel's cadastral municipality selects
`aecs4u-stats.solar.solar_potential_comuni`, a foreign table backed by the
shared Stats database. Annual modern output divided by estimated municipal
capacity gives the yield used to scale the user-supplied system size. The
result is explicitly municipality-level context, not parcel, building or roof
production. Financial defaults are illustrative and all financial assumptions
are editable. The released `aecs4u_stats.pvgis.economics` calculator is used
from the shared venv. The dossier recomputes the scenario server-side from the
same Stats municipal view and includes assumptions, results, the annual
cash-flow table and chart. Focused local tests cover source selection, no-data
handling, calculation output and dossier rendering. Provisioned-host behavior
and rendering remain unverified.

## Scope
- Select the municipality's PVGIS-derived potential through the
  `aecs4u-stats` `solar.solar_potential_comuni` foreign table and label it as
  municipal context.
- Scenario endpoint and panel form for installed capacity, installation cost,
  retail and export prices, self-consumption share, maintenance, degradation,
  discount rate and lifetime.
- Headline NPV, LCOE, simple and discounted payback, year-one production and
  an annual cash-flow table.
- State that capacity is a user input and municipal yield is not a parcel or
  roof estimate; do not infer roof coverage, shading, structural suitability
  or installable capacity.

## Acceptance Criteria
- [x] Assumptions are visible and editable in the Energy tab.
- [x] The endpoint resolves the selected parcel's cadastral municipality and
  reads its PVGIS-derived potential through the Stats foreign table; it never
  scales capacity from parcel area.
- [x] The panel discloses the municipal yield method, spatial resolution,
  dataset version and update time when available.
- [x] The panel shows NPV, LCOE, simple and discounted payback, and annual
  cash flows.
- [x] The dossier optionally carries the current validated scenario and
  recomputes it server-side; the ordinary GET report remains available without
  scenario inputs.
- [x] The released shared calculator is used from the updated shared venv and
  matches the documented 5 kWp reference case: NPV €11,562.1462, LCOE
  €0.0643136/kWh, simple payback 5.11933 years and discounted payback
  5.85343 years.
- [x] Local tests verify municipal source selection, unavailable source
  responses and a dossier with the annual cash-flow chart, table and provenance
  (`tests/test_solar_economics.py`, `tests/test_parcel_dossier.py`).
- [ ] Verify those responses and a rendered scenario PDF through a provisioned
  serving host.

## Dependencies
The serving environment must expose `solar.solar_potential_comuni` through
`aecs4u-stats` and install `aecs4u_stats.pvgis.economics`. Both are available
in the shared venv used for local implementation and verification.

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 9 and §4 Phase 4. The municipal
view is based on PVGIS-derived aggregates and does not report measured roof
output. Default financial values are examples only and must not be presented
as a quote, appraisal, or return guarantee. The model excludes taxes,
incentives, financing and inverter replacement. Self-consumption is a
user-supplied annual share, not an hourly load simulation.
