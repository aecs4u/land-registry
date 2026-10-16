# Public Parcel API v2

The versioned parcel lookup is served at `GET /api/v2/parcels/{fid}`. The
FastAPI OpenAPI document at `/openapi.json` is the authoritative request and
response envelope schema. `GET /api/v2/catalog` publishes supported parcel
sections, stable data fields with descriptions, data shapes, matching methods,
spatial resolution, freshness notes, and source reuse metadata.
`/data-sources` presents provider information and its review basis in the web
interface, distinguishing reviewed access or data-use policies from a named
reuse licence. A review label does not itself grant permission to redistribute
source data.

The catalog endpoint is public and does not consume an API key quota. Fields
marked `provider_dynamic_fields: true` follow the configured provider schema;
the catalog describes their containing data shape and explains the variability
in `data_notes`.

## Authentication and keys

Sign in with a Land Registry account, then manage keys through the authenticated
account API:

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/v1/user/api-keys` | List the caller's keys and metadata |
| `POST` | `/api/v1/user/api-keys` | Create a `parcels:read` key from `{"name":"QGIS"}` |
| `DELETE` | `/api/v1/user/api-keys/{key_id}` | Revoke one of the caller's keys |

The creation response contains the raw secret once. Store it securely; later
list responses expose only its prefix. The active-key maximum defaults to five
and is configurable with `PUBLIC_API_MAX_ACTIVE_KEYS`.

Send the secret in the `X-API-Key` header:

```http
X-API-Key: landregistry_live_<secret>
```

For example:

```sh
curl -H 'X-API-Key: landregistry_live_<secret>' \
  'https://example.invalid/api/v2/parcels/42?include=basic,geometry'
```

`GET /health` remains unauthenticated. Parcel requests without a valid active
key receive `401`; a key without `parcels:read` receives `403`.

Request the annual Black Marble radiance with `include=nightlights`. The
section uses NASA VNP46A4.002 and returns `intensity_nw_cm2_sr` in
nW/(cm² sr) (`units` is also provided), observation year, valid-pixel count,
quality policy and sampling method. It averages quality-flag-0 pixels whose centres fall inside the parcel;
when none fall inside, it uses and labels the representative-point pixel.
Subpixel parcels can therefore share one grid value. Missing quality data and
locations outside the imported raster are reported as unmatched, never as
zero. The product grid is 15 arc-seconds (about 463 m north-south), so the
value is a broad-area proxy rather than a parcel or building measurement.

The application reads an annual Italy mosaic from
`VIIRS_NIGHTLIGHTS_RASTER_PATH` or the newest
`VNP46A4_*_italy.tif` under `VIIRS_NIGHTLIGHTS_DATA_DIR` (default
`/data/viirs/nightlights`). Build it with
`scripts/import_viirs_nightlights.py --year <year> --apply` and an authorized
`LAADS_DAAC_TOKEN`, or `--build-only` from downloaded HDF5 tiles. The source
catalog publishes NASA's product citation and data-use-policy link; no
separate named licence is asserted by this application. The reader accepts
only a single-band WGS84 raster tagged as VNP46A4.002 with its observation
year, UTC import time, physical units, applied 0.1 scale factor, zero offset,
quality-flag-0 policy and fill-value nodata metadata. Missing or mismatched
tags make the source unavailable instead of trusting the filename or assuming
the pixels were scaled and quality-filtered correctly.

Request the `terrain` section for INGV TINITALY 1.1 terrain summaries. Polygon
values use valid 10 m cell centres; the response includes elevation range and
standard deviation, Horn mean/maximum slope, predominant aspect and mean Riley
TRI, plus sample count, coverage, source DOI and match method. A polygon with no
interior cell centre uses a labelled representative-point fallback. Point
samples and the fallback report `coverage_pct: null`, since no polygon share
can be measured. TINITALY
is a bare-earth model, not a building or roof surface. The regional importer
and query are pending release from `aecs4u-stats#28`; until the requested
region's store is installed, the section reports unavailable rather than zero.

The `valuation` section keeps the OMI quote release separate from the geometry
used to select a zone. A local match may include `zone.boundary_source`,
`zone.boundary_vintage`, `zone.match_method` and
`zone.spatial_resolution`. The current local mirror is from 2018/2 and is
approximate where boundaries have changed; its reuse terms remain under review.
PostgreSQL matches report `PostGIS ST_Covers`; the boundary vintage is omitted
when that source does not provide it. `/api/v2/catalog` documents these fields.

## Quotas

Each key receives a shared UTC-hour quota. The default is 10,000 requests per
key and can be changed centrally with `PUBLIC_API_REQUESTS_PER_HOUR`. Quota
counters are consumed atomically in PostgreSQL, so concurrent app instances
enforce the same limit. Responses include `X-RateLimit-Limit`,
`X-RateLimit-Remaining` and `X-RateLimit-Reset`; `429` responses also include
`Retry-After`.

The app additionally applies a 600-request-per-minute per-IP burst guard in
each process. This protects the API from short bursts; it is not the account
quota. Keys and shared counters are unavailable when the PostgreSQL account
store is not configured, and the API fails closed with `503`.

## Parcel lookup

`fid` is the stable cadastral feature ID. The `include` query parameter accepts
comma-separated sections; the default is `basic,geometry`. The response keeps
section-level source, stable `source_key` (when the source is catalogued),
version, match method, coverage and availability fields where the source
supplies them. A missing enrichment is returned with an
explicit unavailable reason. An optional provider failure is isolated to its
section; other requested sections can still be returned, and failed sections
report a `*_lookup_failed` reason. Risk currently reports available seismic,
parcel-centroid MPS04 PGA, parcel-hazard, subsidence and coastal-change
components independently when their stores are provisioned. MPS04 reports its
nearest-grid method, distance and no-match state. The optional municipal
`risk.data.composite_score` uses `municipal_hazard_index_v1` when the upstream
helper is installed and at least one municipal component is available; the
section includes its version, complete/partial status, component values and
scope. It is a heuristic context index, not a probability, expected loss,
parcel estimate or engineering assessment. If no municipal inputs are
available, the score is null while parcel components retain their own states.
The risk section also includes `protection_constraints` when the upstream
`spatial.protection_area` table is provisioned. It reports matched source
records, separate landscape and cultural-heritage coverage, binding status and
whether results were capped. A catalogue-only record is not a binding
constraint; unknown coverage never implies that a parcel is unconstrained.
Provider reuse terms for the aggregate protection sources remain under review.

Request `anncsu_addresses` to match ANNCSU civic access points to the parcel
geometry. The section includes civic-number components, coordinate method,
source vintage and the municipality's share of address records with
coordinates when available. Municipalities supply ANNCSU coordinates; a
missing spatial match does not establish that the parcel has no address.
Candidate or result caps are returned as a partial state.

On a cache miss, the app skips independent adapter calls for enrichment
sections that were not requested: OMI quotes for `valuation`, the MEF profile
for `economics`, SISTER for `buildings`/`address`, OpenData for `opendata`, PVP
for `pvp`, ANNCSU for `anncsu_addresses`, and local raster reads for
`nightlights` and `terrain`. The shared municipality
and parcel-context query may still include joined OMI or tax data. A partial
cold result is not written to the complete parcel read-model cache; an existing
complete cached profile can serve any requested subset.

When a section's source label matches the maintained provider catalog, its
envelope also includes the provider `source_key`, URL, licence/reuse status, terms link and
attribution text when known. Unmatched sources retain their runtime source
label and remain explicitly uncatalogued. Mixed-source and ambiguous labels do
not inherit one provider's licence metadata.

The `solar` section is a Zornade municipality-wide aggregate served through
`aecs4u-stats`; it is not a parcel, building or roof estimate. Matched sections
use `source_key: zornade_municipal_solar` and pass through the source model
version and update time when supplied. Zornade's public guide describes its
municipality/province aggregates as open-licensed but does not name a licence,
so the catalog records that distinction and asserts no formal licence. A
PVGIS-SARAH3 model vintage does not establish reuse terms for the aggregate.

The `cadastral` section also includes `field_provenance`, since its fields come
from more than one source. Each mapped field names its source, catalog key when
available, match method and spatial resolution. For example, `foglio` maps to
the Agenzia delle Entrate INSPIRE cadastral extract at parcel level, while
`postal_code` maps through the ISTAT municipality crosswalk. This mapping
describes the app's current field assembly; it does not certify the terms for
unmapped fields or provider-native OpenData records.

Each record in the `opendata` section includes a stable `provenance` object
alongside its source-native fields. It reports the record ID, endpoint,
retrieval store, recorded/query timestamps, cadastral match method, access
provider, stated data origin and a `reuse` object with licence, terms link,
attribution and status. `provider.status` and `data_origin` basis distinguish
catalogued, uncatalogued, unknown and provider-stated-but-not-record-specific
information; `data_origin.record_specific` makes that distinction machine-readable.
The OpenData PostgreSQL adapter uses OpenAPI Catasto; OpenAPI's [FAQ](https://console.openapi.com/it/apis/catasto/faq)
identifies Agenzia delle Entrate as its principal source, but the historical
query rows do not identify the source for each individual result. OpenAPI's
[published API terms](https://openapi.it/documenti/Termini-e-condizioni-Openapi.pdf)
restrict mass extraction and other uses, and do not establish a general licence
for redistribution. The API therefore reports reuse as unconfirmed and does
not infer permission from a provider name. SISTER cache records use the SISTER
catalog entry, which likewise does not assert downstream reuse rights. Review
the relevant account agreement and source terms before redistributing these
records.

Use `/api/v2/catalog` for the current section list and field/source metadata,
and `/openapi.json` for the request and response envelope schema. Raw cadastral,
census, SISTER, OpenData, PVP and OMI source records may contain provider-native
properties that vary by source release or deployment; the catalog identifies
those sections instead of presenting their properties as a fixed contract. The
OpenData catalog separately documents the stable `records[].provenance.*`
fields while marking provider-native result properties dynamic.
