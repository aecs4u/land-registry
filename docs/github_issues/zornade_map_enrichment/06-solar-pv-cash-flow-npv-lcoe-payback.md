## Summary
Add an editable PV cash-flow scenario to the parcel panel, using a PVGIS
long-term yield at the selected parcel's centroid and financial assumptions
that the user can inspect and change. The user supplies the system size and
surface orientation; parcel area is not a proxy for usable roof area.

**Progress (2026-10-16):** the app now has
`POST /api/v1/enrichment/solar-economics/{national_reference}` and a lazy
Energy-tab form. It returns year-one generation, annual cash flows, NPV, LCOE,
simple and discounted payback, plus PVGIS and assumption provenance. The PVGIS
query uses a 1 kWp reference system with user-specified tilt, aspect and system
losses; user-specified capacity scales the output. Financial defaults are
illustrative and all assumptions are editable. The shared
`aecs4u_stats.pvgis.calculate_solar_economics` implementation is used when
available; a compatibility calculation remains in the app for installed stats
wheels that predate issue #31. The shared package change is still uncommitted
and upstream #31 remains open. On 2026-10-16, the app's shared-venv client made
a live request to the official PVGIS endpoint for a Rome sample and parsed
1,425.2 kWh/kWp/year across twelve monthly rows from PVGIS 5.3/SARAH3. This
checks the current response shape, not a provisioned-host panel scenario. The
parcel dossier can also include the currently calculated scenario: the map
sends only validated inputs and the server recomputes PVGIS yield and
economics before rendering assumptions, results, the annual cash-flow table
and chart. Focused local tests now cover the route's 504 timeout and 503
unavailable responses, and compile a scenario dossier containing the
assumptions, cash-flow chart/table and source metadata. Provisioned-host
behavior and rendering remain unverified.

## Scope
- PVGIS PVcalc yield from the parcel centroid using the selected system's
  declared tilt, aspect and losses; label it as a location estimate.
- Scenario endpoint and panel form for installed capacity, installation cost,
  retail and export prices, self-consumption share, maintenance, degradation,
  discount rate and lifetime.
- Headline NPV, LCOE, simple and discounted payback, year-one production and
  an annual cash-flow table.
- State that capacity and orientation are user inputs; do not infer roof
  coverage, shading, structural suitability or installable capacity.

## Acceptance Criteria
- [x] Assumptions are visible and editable in the Energy tab.
- [x] The endpoint uses a canonical parcel centroid and never scales capacity
  from parcel area.
- [x] The panel shows NPV, LCOE, simple and discounted payback, and annual
  cash flows.
- [x] The dossier optionally carries the current validated scenario and
  recomputes it server-side; the ordinary GET report remains available without
  scenario inputs.
- [x] The app parser accepts a live PVGIS 5.3/SARAH3 response with twelve
  monthly production rows (Rome sample, 2026-10-16).
- [x] The app compatibility calculation matches the sibling checkout's
  `aecs4u_stats.pvgis` reference case for 5 kWp, 1,500 kWh/kWp/year and the
  documented assumptions: NPV €11,562.1462, LCOE €0.0643136/kWh, simple
  payback 5.11933 years, discounted payback 5.85343 years, with zero annual
  cash-flow difference.
- [ ] Confirm that parity through the released, installed shared package after
  `aecs4u-stats#31` lands; the shared venv currently has 0.2.1 without the
  economics module.
- [x] Local tests verify distinct timeout/unavailable responses and compile a
  scenario dossier with the annual cash-flow chart, table and provenance
  (`tests/test_solar_economics.py`, `tests/test_parcel_dossier.py`).
- [ ] Verify those responses and a rendered scenario PDF through a provisioned
  serving host.

## Dependencies
- aecs4u/aecs4u-stats#31 (release the shared calculation; the app compatibility
  implementation keeps the scenario available while that release is pending)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 9 and §4 Phase 4. PVGIS reports a
long-term location yield, not measured roof output. Default financial values
are examples only and must not be presented as a quote, appraisal, or return
guarantee. The model excludes taxes, incentives, financing and inverter
replacement. Self-consumption is a user-supplied annual share, not an hourly
load simulation. The endpoint targets the documented [PVGIS 5 API](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/api-non-interactive-service_en)
and SARAH3. JRC currently labels the newer [PVGIS 6 API](https://photovoltaic-geographic-information-system.ec.europa.eu/en/about-pvgis-6)
beta and under development; revisit the API version when it stabilizes.
