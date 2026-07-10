# Requirements Document — Urban Address / Coordinate Classifier

## 1. Objective

Build a Python application that determines whether a user-supplied **address** or **latitude/longitude coordinate** should be classified as:

- `urban`
- `not_urban`
- `unknown`

The classification must be based primarily on **GHSL / DEGURBA-compatible spatial data**, not on reverse geocoding text labels.

The implementation must be suitable for local development and execution from **VS Code + Codex CLI**.

---

## 2. Background

The application should use the following conceptual model:

- **DEGURBA** (Degree of Urbanisation) is the classification methodology for identifying urban versus rural areas.
- **GHSL** (Global Human Settlement Layer) provides geospatial datasets, including settlement model rasters, that can operationalize this classification.
- Address geocoding is only used to convert an address into a point coordinate.
- The urban/non-urban decision must come from the **GHSL settlement raster** or another explicitly supported urban-area layer.

---

## 3. Scope

### In scope

1. Accept an input as either:
   - a free-form address string, or
   - a latitude/longitude pair.
2. Resolve address input into geographic coordinates.
3. Sample a local GHSL raster dataset at the resolved point.
4. Convert the sampled class into a final label.
5. Expose the functionality through:
   - a Python library API,
   - a command-line interface,
   - optional batch processing for CSV/JSONL.
6. Return structured output suitable for downstream automation.
7. Include tests and clear developer documentation.

### Out of scope (initial version)

1. Browser UI / web frontend.
2. Automatic downloading of global rasters unless explicitly enabled as an optional feature.
3. Municipal legal zoning or land-use compliance decisions.
4. Reverse-geocoding-based urban inference.
5. Training any machine learning model.

---

## 4. Supported Data Model

### Primary dataset

Use a **local GHSL GHS-SMOD raster** as the primary classification source.

The implementation must support the GHSL **Level 2** class values below:

| Code | Label |
|---|---|
| 30 | urban centre |
| 23 | dense urban cluster |
| 22 | semi-dense urban cluster |
| 21 | suburban or peri-urban |
| 13 | rural cluster |
| 12 | low density rural |
| 11 | very low density rural |
| 10 | water |

### Classification modes

The application must support at least two classification modes:

#### `broad`
Treat the following as `urban`:
- 30
- 23
- 22
- 21

Treat the following as `not_urban`:
- 13
- 12
- 11
- 10

#### `strict`
Treat the following as `urban`:
- 30
- 23
- 22

Treat the following as `not_urban`:
- 21
- 13
- 12
- 11
- 10

### Unknown handling

Return `unknown` when:
- the point falls outside the raster extent,
- the raster has nodata at that location,
- geocoding fails,
- the raster value is unsupported or cannot be interpreted.

---

## 5. Functional Requirements

### FR-1 Input modes

The application must accept one of the following inputs:

1. `--address "<free form address>"`
2. `--lat <value> --lon <value>`
3. batch file input with multiple records

It must reject invalid combinations, such as missing longitude when latitude is provided.

### FR-2 Geocoding

When an address is provided, the application must geocode it to coordinates.

#### Requirements
- Support a pluggable geocoding provider abstraction.
- Provide an initial implementation using **Nominatim-compatible geocoding**.
- Require a configurable **user agent** string.
- Allow the geocoder to be disabled in fully offline mode.
- Return a meaningful error when geocoding yields no result.

### FR-3 Raster sampling

The application must:
- open a GHSL raster from a local filesystem path,
- transform the input WGS84 coordinate to the raster CRS,
- sample the raster value at the point,
- detect nodata,
- return the raw class code.

### FR-4 Classification

The application must convert the sampled class code into:
- `status`: `urban`, `not_urban`, or `unknown`
- `class_code`: numeric code or `null`
- `class_label`: human-readable text
- `mode`: `broad` or `strict`

### FR-5 Structured output

The CLI must support at least:
- human-readable text output
- JSON output

#### Required JSON shape

```json
{
  "input": {
    "address": "Piazza Maggiore, Bologna, Italy",
    "lat": 44.4939,
    "lon": 11.3430
  },
  "resolved_point": {
    "lat": 44.4939,
    "lon": 11.3430
  },
  "dataset": {
    "type": "GHSL_GHS_SMOD",
    "path": "/data/ghsl/GHS_SMOD_...tif"
  },
  "classification": {
    "mode": "broad",
    "status": "urban",
    "class_code": 30,
    "class_label": "urban centre"
  },
  "meta": {
    "geocoded": true,
    "provider": "nominatim",
    "timestamp": "2026-04-06T12:00:00Z"
  }
}
```

### FR-6 Batch mode

The application should support batch classification for a CSV or JSONL file.

#### CSV minimum supported columns
- `id` (optional but recommended)
- `address` or
- `lat` and `lon`

#### Batch output requirements
- preserve input identifiers,
- append classification fields,
- write output as CSV and/or JSONL,
- continue processing even if some records fail,
- include per-record error messages where relevant.

### FR-7 Logging

The application must log:
- startup configuration summary,
- geocoding attempts,
- raster path opened,
- coordinate transformation steps at debug level,
- classification result,
- errors and warnings.

### FR-8 Library API

Expose a Python API with a shape similar to:

```python
result = classify_location(
    ghsl_path="/data/ghsl/ghs_smod.tif",
    lat=44.4939,
    lon=11.3430,
    mode="broad",
)
```

and

```python
result = classify_location(
    ghsl_path="/data/ghsl/ghs_smod.tif",
    address="Piazza Maggiore, Bologna, Italy",
    mode="strict",
    geocoder="nominatim",
)
```

### FR-9 Configurability

The application must support configuration through:
- CLI flags,
- environment variables,
- optional `.env` file.

Minimum configurable items:
- `GHSL_RASTER_PATH`
- `GEOCODER_PROVIDER`
- `GEOCODER_USER_AGENT`
- `GEOCODER_TIMEOUT`
- `CLASSIFICATION_MODE`
- `OUTPUT_FORMAT`
- `LOG_LEVEL`

### FR-10 Error handling

The application must fail gracefully for:
- invalid coordinates,
- unreadable raster path,
- unsupported CRS,
- geocoder timeout,
- geocoder quota / policy failures,
- malformed batch file,
- unsupported raster values.

It must return a non-zero exit code on fatal CLI errors.

---

## 6. Non-Functional Requirements

### NFR-1 Language and runtime
- Python 3.11+ preferred.
- Must run on Linux.
- Should also work on macOS and Windows if dependencies allow.

### NFR-2 Performance
- Single-record classification should complete quickly when the raster is local.
- Batch mode should avoid reopening the raster for every row.
- The implementation should support processing at least several thousand rows without excessive memory use.

### NFR-3 Reliability
- Deterministic results for the same raster and input.
- Clear distinction between `unknown` and `not_urban`.

### NFR-4 Maintainability
- Typed Python code where practical.
- Modular architecture.
- Unit and integration tests.
- Developer-friendly README.

### NFR-5 Offline-first behavior
- Coordinate classification from an existing lat/lon and local raster must work fully offline.
- Address mode may require network access unless an offline geocoder is later added.

---

## 7. Proposed Project Structure

```text
urban-classifier/
├── pyproject.toml
├── README.md
├── .env.example
├── src/
│   └── urban_classifier/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── models.py
│       ├── classify.py
│       ├── geocoders/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   └── nominatim.py
│       ├── raster/
│       │   ├── __init__.py
│       │   └── ghsl.py
│       ├── batch.py
│       └── logging_utils.py
├── tests/
│   ├── test_classification.py
│   ├── test_cli.py
│   ├── test_batch.py
│   ├── test_geocoder.py
│   └── fixtures/
│       ├── sample_points.csv
│       └── small_test_raster.tif
└── docs/
    └── architecture.md
```

---

## 8. Suggested Implementation Details

### Recommended libraries
- `rasterio` for raster IO
- `pyproj` for CRS transformations
- `pydantic` or `dataclasses` for typed models
- `typer` or `argparse` for CLI
- `geopy` or direct HTTP client for geocoding abstraction
- `python-dotenv` for environment loading
- `pytest` for tests

### Packaging
Use `pyproject.toml` and produce an installable package with a console entry point such as:

```toml
[project.scripts]
urban-classifier = "urban_classifier.cli:app"
```

or equivalent for the chosen CLI framework.

---

## 9. CLI Requirements

### Single coordinate example

```bash
urban-classifier classify \
  --ghsl /data/ghsl/GHS_SMOD.tif \
  --lat 44.4949 \
  --lon 11.3426 \
  --mode broad \
  --output json
```

### Address example

```bash
urban-classifier classify \
  --ghsl /data/ghsl/GHS_SMOD.tif \
  --address "Piazza Maggiore, Bologna, Italy" \
  --mode strict \
  --output json
```

### Batch example

```bash
urban-classifier batch \
  --ghsl /data/ghsl/GHS_SMOD.tif \
  --input data/addresses.csv \
  --output results.jsonl \
  --mode broad
```

### CLI behavior
- `classify` subcommand for single item
- `batch` subcommand for multiple items
- `validate` subcommand optional, to validate input files and environment
- `--help` must be clear and complete

---

## 10. Data and Coordinate Requirements

### Input CRS
- User-supplied coordinates are assumed to be WGS84 (`EPSG:4326`).

### Raster CRS
- The code must detect the raster CRS dynamically.
- The code must transform WGS84 input into the raster CRS before sampling.

### Dataset validation
At startup or on first use, the application should validate:
- the raster exists,
- the raster can be opened,
- CRS is available,
- nodata metadata is readable,
- band count is compatible,
- datatype is acceptable.

---

## 11. Testing Requirements

### Unit tests
Must cover:
- class-code mapping,
- strict vs broad mode,
- nodata handling,
- unsupported code handling,
- argument validation,
- environment variable loading.

### Integration tests
Must cover:
- coordinate classification using a small test raster,
- address classification with mocked geocoder,
- batch mode with mixed valid/invalid rows,
- JSON output schema.

### CLI tests
Must cover:
- success exit code,
- fatal error exit code,
- invalid argument combinations,
- machine-readable JSON output.

---

## 12. Acceptance Criteria

The implementation is accepted when all of the following are true:

1. A user can classify a single coordinate from the CLI.
2. A user can classify a single address from the CLI.
3. The application samples a local GHSL raster and returns the correct Level 2 class code.
4. The application supports both `broad` and `strict` modes.
5. The application produces valid JSON output in the required shape.
6. Batch mode works on a CSV with at least 100 rows.
7. All tests pass.
8. The README explains installation, configuration, data requirements, and usage examples.
9. Errors are explicit and actionable.
10. The implementation does not infer urban status from reverse-geocode text alone.

---

## 13. README Requirements

The generated README must include:
- what GHSL / DEGURBA are in one short paragraph,
- how to install dependencies,
- how to obtain a compatible GHSL raster,
- how to configure geocoding,
- examples for single address, single coordinate, and batch mode,
- caveats about data resolution and peri-urban classification,
- explanation of `broad` versus `strict`.

---

## 14. Nice-to-Have Features (Not Required for MVP)

1. Automatic tile or dataset download helper.
2. Caching geocoding results.
3. Support for alternative geocoders.
4. Optional polygon lookup against municipal "urbanized area" layers.
5. FastAPI wrapper around the core library.
6. Docker support.
7. Optional QGIS plugin wrapper.
8. Confidence / provenance metadata.

---

## 15. Implementation Notes for Codex CLI

When implementing:

1. Prefer small, well-tested modules over a monolithic script.
2. Keep classification logic isolated from geocoding.
3. Treat geocoding as optional and replaceable.
4. Make the CLI ergonomic for both developers and batch workflows.
5. Avoid hidden network calls unless the user explicitly requests geocoding.
6. Preserve a strict separation between:
   - input resolution,
   - raster sampling,
   - class interpretation,
   - output formatting.
7. Include mocked tests for all network-dependent paths.
8. Default to safe failure and explicit diagnostics.

---

## 16. Optional Future Extension: Administrative DEGURBA

A future version may also support classifying **administrative units** rather than single points, using an official DEGURBA administrative layer.

That feature is out of scope for the MVP, which focuses on **point-level classification using GHS-SMOD grid cells**.

---

## 17. Deliverables

Codex CLI should generate:

1. complete Python project source code,
2. `pyproject.toml`,
3. README,
4. `.env.example`,
5. tests,
6. example input files,
7. a short architecture note,
8. optional Makefile or task runner commands.

---

## 18. Final Instruction

Implement the MVP first.

Priority order:
1. coordinate classification,
2. address geocoding,
3. JSON output,
4. batch mode,
5. tests and packaging,
6. developer polish.
