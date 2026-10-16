## Summary
Status: implemented for the current panel metrics.

The panel reports absolute figures — population density, mean taxable income,
€/m² — with no reference point. A reader without domain knowledge cannot tell
whether a value is ordinary or remarkable. Show the national (or regional)
benchmark next to the metric.

## Scope
- Versioned national means/medians from the same MEF/ISTAT releases already
  consumed. Prefer a same-tax-year MEF national aggregate; use the static 2022
  fallback only for 2022 values, and omit the income benchmark when vintages do
  not match.
- `parcel-enrichment.js`: render the comparison inline with the value, not as a
  separate card.
- Pick the comparison basis deliberately per metric. A national mean is the
  wrong reference for a metric whose distribution is strongly regional; prefer
  the regional benchmark in those cases and say which one is shown.

## Acceptance Criteria
- Density, income, and €/m² each display a benchmark alongside the parcel value.
- The label states what the benchmark is and its reference year.
- A metric with no sensible benchmark renders unchanged.

## Dependencies
- None blocking; benchmark figures derive from releases already ingested.

## Notes
Implements `docs/MAP_SRS.md` MAP-FR-053 (Should) and the §8 guideline added in
v1.2. Low effort relative to how much it improves readability of the panel.

The implemented basis is:

- census density: static national 2021 population-density benchmark, displayed
  only when section area is available;
- mean income: same-tax-year national MEF/IRPEF mean when available, with the
  official static 2022 value (€22,810) used only for 2022 data; the benchmark
  is labelled as a national per-taxpayer mean. The figure comes from the MEF
  [historical IRPEF table](https://www1.finanze.gov.it/finanze/stat_dbNewSerie/public/report_d.php?req_anno=SERI&req_back=SERI&req_classe=01&req_contrib=PFTOT&req_media=si&req_pag=1&req_sub_tema=02&req_tema=01),
  which reports €22.81 thousand for average taxable income in 2022;
- OMI sale €/m²: median of municipal OMI quotes for the selected quote's exact
  property type, condition and reference semester. It is shown with the period
  and count of comparable zones, and omitted when fewer than two zones qualify.
  A single national property-price benchmark would be misleading.
