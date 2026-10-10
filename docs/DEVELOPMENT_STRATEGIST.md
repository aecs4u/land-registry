# Real-estate development strategist: PoC strategy

This document defines the opportunity-identification strategies to evaluate
before implementing the optional module described by
[`real-estates_development_strategist.md`](real-estates_development_strategist.md).
The first deliverable is a PoC and a reviewable shortlist. Production API,
module boundaries, and the Ocular workflow contract will be decided after the
PoC results are available.

## Decision the score should support

The ranking should help a developer decide which parcels deserve a closer
feasibility review. It is a screening tool. It should not claim that a parcel
is legally buildable, accurately valued, or profitable from a map score alone.

Separate each result into three parts:

1. **Eligibility:** can the parcel be considered with the zoning and constraint
   data available?
2. **Opportunity:** how much plausible development headroom and location
   advantage does it have?
3. **Evidence quality:** how complete and reliable are the parcel, zoning,
   building, transit, and imagery inputs?

Do not let a high opportunity score erase a failed legal or physical
eligibility check. Keep hard exclusions and risk flags visible beside the
ranking.

## Opportunity strategies to compare

The PoC should score the same pilot parcels using three profiles. These seed
weights are starting hypotheses for comparison, not production defaults.

| Criterion | Redevelopment capacity | Transit-oriented growth | Infill and underuse |
| --- | ---: | ---: | ---: |
| Residual buildable area | 0.50 | 0.30 | 0.40 |
| Transit accessibility | 0.15 | 0.35 | 0.15 |
| Neighborhood density / demand proxy | 0.15 | 0.20 | 0.15 |
| Low existing site coverage | 0.20 | 0.15 | 0.30 |

All criteria are normalized to 0–1, with 1 representing the more attractive
value. Residual capacity and the neighborhood proxy are maximized. Distance to
transit and current site coverage are minimized. Elicit pairwise preferences
from the intended decision-makers with AHP, calculate the Consistency Ratio,
and revisit a matrix when CR exceeds 0.1. Compare AHP weights with the seed
profiles and a simple residual-capacity-only baseline.

### 1. Redevelopment capacity

Find parcels where allowed planning capacity materially exceeds existing
development. Prioritize positive residual floor area, then favor parcels with
low existing coverage and useful access to services and transit. This is the
main SRS strategy and should be the PoC baseline.

### 2. Transit-oriented growth

Find parcels with development headroom near useful transit. Test whether
transit distance should be straight-line distance or a network travel-time
measure. Include neighborhood density as a context or demand proxy, not as a
direct substitute for household demand or market absorption.

### 3. Infill and underuse

Find vacant or lightly covered parcels that can support feasible infill. The
PoC should inspect parcel area and shape alongside the SRS score. In a later
phase, contiguous parcels can be evaluated as an assembled site when one
parcel alone is too small or irregular. Keep that site-assembly score separate
from individual parcel ranks.

## Candidate gates and measures

Use gates before ranking. The PoC should record why a parcel was excluded or
left unverified.

| Gate or measure | PoC rule | Notes |
| --- | --- | --- |
| Parcel identity and geometry | Require a stable parcel reference and valid polygon | Retain source and retrieval date |
| Zoning | Require a known zone and allowed use for a verified candidate | Missing zoning means `unverified`, not eligible by assumption |
| Residual capacity | Require positive permitted capacity after existing floor area and known constraints | Apply a documented tolerance for measurement noise |
| Constraints | Flag or exclude protected areas, setbacks, hazards, and access limits when authoritative data is available | Distinguish legal exclusions from scored risks |
| Existing site coverage | Detected building footprint area / parcel area | This is lot coverage, not conventional FAR |
| Residual buildable area | Permitted gross floor area − existing gross floor area | If only footprints are available, label the footprint-based result as a proxy |
| Transit accessibility | Distance or travel time to a selected transit node | Record the node definition and source date |
| Neighborhood context | H3 cell built-up ratio for the first PoC | Compare with population, jobs, or housing-demand data if available |
| Evidence quality | Coverage and confidence for each input | Show the reason for low confidence; do not hide it in the score |

The SRS defines `current_far` as building area divided by parcel area. That
formula measures site coverage when building floors are unknown. The PoC must
test whether Ocular can return footprint polygons and a useful height or floor
count estimate. If it cannot, report `estimated_site_coverage` and avoid
presenting it as gross-floor-area FAR.

The advertised Agenzia delle Entrate cadastral WFS supplies parcel geometry,
not a complete municipal planning model. Treat PUG zoning and maximum-FAR
values as separate pilot inputs with their own source, date, and coverage
checks. Do not infer a zoning rule where the PUG layer is missing.

## Ranking and explanations

For each strategy, produce:

- eligibility state: `eligible`, `excluded`, or `unverified`, with reasons;
- each raw criterion value, normalized value, weight, and weighted contribution;
- total score and rank within the pilot area;
- evidence confidence and dataset dates;
- the strategy profile and weight version used to produce the rank.

Compare robust percentile normalization with winsorized min–max normalization
so that one unusually large parcel does not dominate every other result.
Report ranks across the tested strategies; do not combine the profiles into one
unlabelled universal score. If a criterion is missing, either mark the parcel
unverified for that profile or publish a clearly named partial-data ranking.
Do not silently reweight a partial score and present it as comparable to a
complete score.

## PoC scope and evaluation

1. Select one dense urban area and one lower-density fringe area, each small
   enough for manual review and complete zoning checks.
2. Assemble parcel boundaries, PUG zoning and constraints, recent optical
   imagery, transit nodes, and any available population or employment context.
3. Run the building-footprint extraction through Ocular workflows. Preserve
   workflow/model version, imagery identifiers, output confidence, and any
   manual corrections. Do not build or run local ML models for this PoC.
4. Have a planning or development reviewer label a blind sample as promising,
   not promising, or insufficient evidence, with a short reason.
5. Compare the three profiles, the residual-only baseline, and reviewer
   judgments. Inspect top-ranked parcels and disagreement cases rather than
   relying only on an aggregate score.
6. Perturb weights and normalization choices to see whether the shortlist is
   stable. Record Ocular latency, cost, failure rate, and footprint quality.

Agree numeric acceptance thresholds with the reviewer before running the
PoC. Useful measures include top-k precision against the reviewed shortlist,
fraction of top-ranked parcels with complete zoning evidence, ranking
stability under reasonable weight changes, and time needed to review the
shortlist. The PoC should also record coverage gaps and false positives caused
by footprint errors, stale imagery, overlapping zones, or incomplete transit
data.

## Ocular handoff and implementation decisions

The PoC uses Ocular workflows for footprint extraction. The endpoint,
authentication, request payload, asynchronous run/poll behavior, output
geometry, confidence fields, and handling of imagery assets must follow the
actual Ocular workflow contract. Those details are intentionally left open
until that contract and PoC findings are available.

## Integration with the existing map

The map should render the strategist's results as an analysis overlay that
coexists with the canonical layers. It should not treat the rendered map as
the analysis data API: the analysis reads authoritative, provenance-bearing
inputs and returns its own bounded result set for display. Existing map-layer
catalog, health, MVT, and bounded GeoJSON contracts are documented in
[`CANONICAL_MAP_LAYERS.md`](CANONICAL_MAP_LAYERS.md).

### Existing layers and their role

| Existing layer | Role in opportunity review | Scoring use and limits |
| --- | --- | --- |
| `cadastral-parcels` | Candidate geometry, parcel area, stable `canonical_reference` / national reference, and existing map selection | Primary spatial unit. Use the stable reference to join and restore a parcel. Confirm area and geometry quality before ranking. |
| `cadastral-sheets`, `urban-sections`, `geo-boundaries` | Orientation and cadastral/administrative context | Do not score these boundaries as opportunity criteria. |
| `market-zones` (OMI) | Market context while reviewing a candidate | OMI ranges are zone-level context, not a parcel valuation or development-return estimate. Preserve zone code, typology, source and date. |
| `census-sections` | Population, family, dwelling and building context for neighborhood comparisons | Treat values as section-level aggregates. A parcel-to-section or H3 join is a proxy, not parcel-level demand. |
| `flood-hazard`, `landslide-hazard`, `surface-subsidence`, `seismic-classification`, `mps04-points` | Risk review and potential constraint flags | Apply a hard gate only when the relevant rule and layer are authoritative for that location. Otherwise show a risk/context flag. Point or municipality-scale seismic data is not a parcel engineering assessment. |
| `maritime-concessions` | Coastal-area overlap/context check | An overlap is a prompt for review, not by itself proof that development is prohibited. |
| `solar-potential` | Optional context for a later rooftop/energy strategy | Municipality-level potential is not a parcel-level building estimate and is not part of the three initial profiles. |
| `postal-zones` | Location context | Not a direct opportunity criterion in the initial profiles. |

The map catalog does not currently supply PUG zoning, permitted use, maximum
FAR, setbacks, transit nodes or travel times, or parcel building footprints
and floor counts. These are required or useful PoC inputs, but must come from
separately identified planning, transit, and Ocular workflow sources. Ocular
building footprints should be attached to the analysis result with workflow
version, imagery/source date, and confidence; they should not be written into
the cadastral parcel layer. The absence of a layer or an unavailable layer is
unknown evidence, never a zero value or implicit pass.

### Opportunity result overlay and selection

Each analysis run should produce one result feature per evaluated cadastral
parcel, keyed by the canonical parcel reference and map parcel ID where
available. Keep the strategy profile, profile/weight version, eligibility
state, rank, total score, criterion values and contributions, evidence
quality, exclusion/unverified reasons, and input source dates with the result.
This supports an explainable review without expanding or changing the
canonical `cadastral-parcels` properties.

Render the shortlist as a separate, named **Development opportunities**
overlay for the selected profile. Use a clear rank/score legend and distinct
visual states for eligible, excluded, and unverified candidates. Low evidence
must not look like a low opportunity score. Keep the parcel base layer and
other catalog overlays independently toggleable; the opportunity overlay
should draw above them and should only show candidates returned by the active
analysis. At parcel viewing scales, clicking a result should resolve its
canonical reference through the existing parcel-selection path, highlight the
parcel, and open the existing parcel detail panel. Show the run's score
breakdown and evidence in a separate opportunity-analysis section or panel so
it remains clear which values belong to the analysis and which belong to
canonical parcel enrichment. The ranked result list and map should stay in
sync: choosing a list row selects that parcel on the map, and choosing a map
candidate focuses the matching ranked row and its explanation.

The existing map persists parcel selection as `?parcel=<national-reference>`
and `parcel_id`, and selection survives layer changes. Selecting a result
should use that same behavior rather than creating a second parcel identity or
selection mechanism. Clearing or closing an analysis removes its overlay and
analysis details while retaining the selected parcel, as the current map
selection contract requires. For the PoC, results may be run-scoped and
temporary; a shared URL that restores a complete ranking requires a durable
analysis-run identifier and is a post-PoC persistence decision.

### Delivery and scale

For a PoC, render a bounded, reviewer-sized shortlist as an analysis-owned
GeoJSON overlay, independent of the canonical layer catalog. This keeps
temporary Ocular-derived results out of the allow-listed PostGIS catalog and
avoids implying they are durable source data. Keep the overlay scoped to the
analyzed area, use the map's parcel-level zoom behavior, and respect existing
viewport and feature-count safeguards. Do not fetch a national parcel set into
the browser to calculate or draw ranks.

Before production implementation, use PoC measurements to choose the result
transport: a small run-scoped response may remain GeoJSON; a persisted or
larger result set should use a bounded server-side analysis result endpoint
and, if map scale requires it, a dedicated MVT source. Only promote the
overlay into the canonical layer catalog if the results become durable,
reusable map data with an explicit serving and health contract. In either
case, keep analysis computation and scoring server-side; map visibility,
layer health, or a client-side spatial join must not determine eligibility or
rank.

## Fit with the aecs4u-dse scoring approach

Review of the local `aecs4u-dse` package and its requirements shows a useful
scoring pattern: evaluate eligibility separately, score normalized criteria
with named strategy weights, explain each contribution, and retain the input
and profile versions needed to reproduce a result. That pattern fits this
PoC. The existing DSE package itself does not fit as the strategist's scorer:
it is built for judicial-auction lots, with seven fixed auction sub-scores,
lot-specific KO inputs, and auction-specific adjustments and persistence.

| DSE design | Use in this PoC | Adaptation required |
| --- | --- | --- |
| `PASS` / `FAIL` / `UNKNOWN` gates before scoring, with evidence per rule | Keep eligibility separate from attractiveness and show why a parcel failed or is unresolved | Preserve `unverified` as its own state. Do not inherit DSE's default behavior of scoring `UNKNOWN` candidates as incomplete, or its neutral 0.50 fallback for missing required sub-scores. A missing zoning or building input must not look like average opportunity. |
| Named profiles with normalized, weighted sub-scores and contribution breakdowns | Keep the three development profiles, and report each criterion's value, weight and contribution | Define development-specific criteria and weights. DSE's FLIP/HOLD/LANDBANK weights and its seven score names have auction meanings. |
| Percentile normalization against a recorded reference population | Compare percentile ranking with the winsorized min–max method already in the PoC plan | Choose parcel comparison groups and a fixed reference snapshot. DSE's auction grouping (region, macro-category, rolling 24 months) should not be copied as the land-development population. Keep absolute zoning gates outside percentile normalization. |
| Feature provenance, source dates, confidence and versioned score/profile snapshots | Carry cadastral and planning source releases plus Ocular workflow/model, imagery, confidence and correction metadata with each result | Use strategist-specific feature names and parcel/AOI identities; do not write results into lot-keyed DSE tables. |
| Calibration against auction outcomes, with proposed weights adopted manually and versioned | Borrow the principle that weight changes are reviewed, recorded and reproducible | Auction sale probability and adjudication premium are not evidence of development feasibility. For this PoC, compare profiles against independent planning-review labels; do not fit a rank model. Revisit calibration only when relevant development outcomes exist. |

The current `aecs4u-dse` Ocular adapter maps appraisal fields onto auction KO
inputs; it does not extract building footprints or provide parcel-development
criteria. Keep the PoC's image-to-footprint step in the agreed Ocular workflow
and treat its output as a sourced feature observation, not as a DSE score.
Likewise, omit DSE's optional `aecs4u-ml` prediction and calibration-model
paths in accordance with this PoC's Ocular-workflow preference.

**Recommendation:** reuse DSE's separation of gates, feature calculation,
profile weighting, explanation and provenance as design guidance. Do not add
`aecs4u-dse` as a direct dependency or call its auction `/deals` API for parcel
ranks. After PoC results, consider extracting a domain-neutral weighted-score
contract only if both consumers need the same tested behavior; keep each
domain's eligibility rules, feature definitions and persistence separate.

## Relevant ideas from Builtly Maps

The public [Builtly Maps description](https://www.builtly.ai/maps.html) presents
a plot radar that finds buildable parcels by area and development potential,
then shows the candidates as map clusters with an exportable table. Its
separate [feasibility-study description](https://www.builtly.ai/mulighetsstudie.html)
continues from a parcel shortlist into a site study using zoning, measured
buildable area, neighboring building heights, terrain, daylight and explicit
economic assumptions. The Maps application itself requires login, so these
are product-level references, not a review of its live interface or evidence
about the accuracy of its calculations.

Ideas that fit this strategist:

- **Connect discovery, ranking and review.** Keep the ranked parcel list and
  map selection synchronized, as already specified above. If the PoC later
  expands from a small AOI to municipality-wide discovery, show server-side
  clusters at overview scales and let users drill into individual parcels;
  keep the map and export table based on the same result snapshot.
- **Show an area ledger with its denominator.** Report gross cadastral area,
  known exclusion/setback area, net usable site area, permitted floor area,
  estimated existing floor area, and residual capacity as distinct measures.
  State whether each rule comes from an actual planning boundary or a
  configurable setback distance. This prevents parcel area, site coverage,
  and gross-floor-area FAR from being mixed into one number.
- **Explain the ranking with comparisons.** Alongside each rank, state the
  criteria that drove it and include the relevant values (for example,
  permitted capacity and estimated current coverage). When weight or data
  sensitivity changes the ordering, mark the shortlist as unstable or the
  candidates as effectively tied; leave the final choice to the reviewer.
- **Keep feasibility economics as a separate follow-up.** A later site study
  can compare development scenarios with explicit programme, sale/rent,
  construction-cost and financing assumptions, then show residual land value
  and sensitivity. Do not present that value as part of the current
  opportunity score until the PoC has validated those inputs.
- **Make unknown evidence visible.** Carry the source, date and denominator
  beside each figure; use `unknown` or `not assessed` when the source does not
  answer a question. Ocular may supply building-footprint evidence, while
  spatial and area calculations remain deterministic and auditable.

These are workflow and reporting ideas. Builtly describes Norway/Sweden
coverage and local terms such as BYA/BRA; do not import its planning rules,
typologies, metrics or hazard assumptions into the Italian PUG/cadastral
context without mapping them to authoritative Italian definitions.

## Optional module boundary (conceptual)

After the PoC, the feature belongs in an isolated, opt-in `Development
Strategist` module. Its responsibilities are to:

- accept an AOI or a set of selected parcel references;
- gather the parcel, zoning, constraint, transit, and imagery evidence needed
  for the chosen strategy;
- obtain building extraction from Ocular workflows, with model/workflow
  provenance and confidence carried into the analysis;
- apply eligibility gates, calculate spatial criteria, and rank parcels using
  a named strategy profile;
- return the ranked shortlist with criterion contributions, exclusions,
  evidence quality, and source dates for map display or export.

The module should reuse the app's parcel and map sources where they meet the
PoC's accuracy and provenance requirements. It should not become a second
canonical store for national cadastral or planning data. The feature flag,
request lifecycle, and result transport should be selected after measuring
the PoC's runtime and deciding how Ocular executes workflows. The map
interaction and data-source roles are defined above; PoC results should
validate that contract against real coverage and shortlist size.

After the PoC, decide:

- which opportunity profiles are useful and which should be dropped;
- eligibility gates and tolerances for each data source;
- whether footprint-only coverage is useful enough or floor estimates are
  required;
- the preferred normalization and approved AHP comparisons;
- what to do with incomplete evidence and low-confidence Ocular results;
- whether analysis belongs in a user-triggered API, a background job, or both;
- whether results need durable run history and shareable map URLs;
- whether the measured result size needs a server-side endpoint or vector tiles.

The future application module should remain optional and disabled by default.
Its implementation should only start after these decisions and the Ocular
workflow contract have been reviewed.
