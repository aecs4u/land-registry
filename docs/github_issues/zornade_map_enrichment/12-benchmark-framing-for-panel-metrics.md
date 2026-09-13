## Summary
Status: implemented for the current panel metrics.

The panel reports absolute figures — population density, mean taxable income,
€/m² — with no reference point. A reader without domain knowledge cannot tell
whether a value is ordinary or remarkable. Show the national (or regional)
benchmark next to the metric.

## Scope
- A small static benchmark table of national medians/means, versioned with the
  dataset it summarises. This does not need a new upstream store; the figures
  come from the same MEF/ISTAT releases already consumed.
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
- mean income: static national MEF/IRPEF benchmark with dataset version;
- OMI sale €/m²: median of the currently loaded comparable municipal OMI
  quotes, because a single national property-price benchmark would be
  misleading.
