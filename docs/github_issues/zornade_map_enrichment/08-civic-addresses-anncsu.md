## Summary
Show geocoded ANNCSU civic access points alongside the existing SISTER address
strings, fulfilling the intent already noted in `docs/urban_address_requirements.md`.

**Implementation status (2026-10-17): regional PostgreSQL data provisioned; parcel acceptance partial.**
The app has a dedicated parcel endpoint, lazy ANNCSU card, API v2 section and
Typst dossier section. The panel and dossier distinguish complete, partial
and unknown municipality coordinate coverage from a parcel no-match. The
installed shared reader queries PostgreSQL. Sicilia's
`anncsu.public.r19__addresses` contains 3,165,306 access points, including
2,432,270 geocoded points; addresses, streets, municipality coverage and
provenance are in the source-owned `anncsu` database. PostGIS stores points as
EPSG:4258 with a GiST index. The app adapter returned regional results and
exposed its candidate limit in a Palermo-area smoke query. Exact known-parcel /
source-access-ID validation and national regional coverage remain open. The
existing capped SISTER address section stays separate and clearly labelled as
cadastral-reference matched, cache-dependent and non-geocoded.

**Source review (2026-10-15):** ANNCSU's official [Open Data page](https://www.anncsu.gov.it/it/consultazione-dellarchivio/open-data/)
lists monthly national and regional CSV ZIP downloads. The official
[indirizzario metadata](https://www.anncsu.gov.it/.allegati/metadata_indirizzario.json)
defines the stable `PROGRESSIVO_ACCESSO`, civic-number components, coordinates,
altitude and coordinate-method code. Coordinates are longitude/latitude in the
ETRF2000 realization of ETRS89 at epoch 2008.0 and are stored as EPSG:4258.
The [georeferencing page](https://www.anncsu.gov.it/it/consultazione-dellarchivio/georeferenziazione-numeri-civici/)
says coordinates are available only where municipalities supply them. The
[public consultation terms](https://www.anncsu.gov.it/it/consultazione-dellarchivio/ricerca-strade-e-numeri-civici/)
state CC BY 4.0. The importer retains rows without coordinates and keeps the
coordinate method with each address.

## Scope
- [x] Existing SISTER address strings remain exact-reference matched, capped
  and de-duplicated.
- [x] Add an upstream importer for regional `stradario` and `indirizzario` ZIPs,
  a per-region SQLite/R-tree staging store, PostgreSQL publication with
  provenance and indexed PostGIS geometry, and a local import CLI.
- [x] Add a point-in-parcel query that returns civic components, coordinate
  method, source vintage, CRS, candidate/result cap states and municipality
  coordinate coverage.
- [x] Add `/api/v1/enrichment/anncsu-addresses/{national_reference}`, a lazy
  parcel-panel card, an API v2 section and a dossier section.
- [x] Provision the Sicilia regional store in PostgreSQL and verify that the
  app reader returns data and exposes its candidate cap.
- [ ] Verify a known parcel against its source access IDs, including zero and
  multiple matches and partial candidate caps.

## Acceptance Criteria
- [ ] A known geocoded ANNCSU access point spatially matches its parcel with the
  correct stable access ID and full civic-number components.
- [x] No match and missing regional store remain distinct in focused tests; a
  no-point result never implies that no civic address exists.
- [ ] Verify partial coordinate coverage and candidate-cap state against
  provisioned source records.
- [x] Each returned point carries its coordinate method and EPSG:4258 source
  frame; the block reports source vintage and municipality coordinate coverage
  when the source provides them. The panel and dossier show complete, partial
  or unknown coordinate coverage with the percentage when available, separate
  from a no-match result.
- [x] Panel, API v2 and PDF dossier identify ANNCSU separately from SISTER
  address strings and preserve source attribution.

## Dependencies
- aecs4u/aecs4u-stats#33 tracks review of the shared ANNCSU importer/query;
  Sicilia is provisioned and exact source-access-ID acceptance remains open.

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 14, §4 Phase 4.3, Appendix A.5.3 and
Appendix A.13, plus `docs/anncsu-addresses.md` in the sibling package. The PDF
comparison remains in Appendix A.5.8; its Zornade side stays explicitly
unverified until an exported sample for the same parcel is inspected.
