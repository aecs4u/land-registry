## Summary
Show geocoded ANNCSU civic access points alongside the existing SISTER address
strings, fulfilling the intent already noted in `docs/urban_address_requirements.md`.

**Implementation status (2026-10-16): code implemented, data acceptance pending.**
The app has a dedicated parcel endpoint, lazy ANNCSU card, API v2 section and
Typst dossier section. The panel and dossier explicitly distinguish complete,
partial and unknown municipality coordinate coverage from a parcel no-match.
The sibling `aecs4u-stats` working tree now includes a
regional downloader/importer and an indexed point-in-polygon query. Those
upstream changes are uncommitted and unreleased, and no regional address store
is provisioned on the review host. Live parcel matches and coordinate coverage
therefore remain unverified. The existing capped SISTER address section stays
separate and clearly labelled as cadastral-reference matched, cache-dependent
and non-geocoded.

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
  a per-region SQLite/R-tree store, source provenance and local import CLI.
- [x] Add a point-in-parcel query that returns civic components, coordinate
  method, source vintage, CRS, candidate/result cap states and municipality
  coordinate coverage.
- [x] Add `/api/v1/enrichment/anncsu-addresses/{national_reference}`, a lazy
  parcel-panel card, an API v2 section and a dossier section.
- [ ] Provision a regional store and verify known parcels against source
  access IDs, including zero and multiple matches and partial candidate caps.

## Acceptance Criteria
- [ ] A known geocoded ANNCSU access point spatially matches its parcel with the
  correct stable access ID and full civic-number components.
- [ ] No match, missing coordinates, partial coordinate coverage and missing
  regional store remain distinct states; never imply that no point proves no
  address exists.
- [x] Each returned point carries its coordinate method and EPSG:4258 source
  frame; the block reports source vintage and municipality coordinate coverage
  when the source provides them. The panel and dossier show complete, partial
  or unknown coordinate coverage with the percentage when available, separate
  from a no-match result.
- [x] Panel, API v2 and PDF dossier identify ANNCSU separately from SISTER
  address strings and preserve source attribution.

## Dependencies
- aecs4u/aecs4u-stats#33 remains open until the local changes are reviewed,
  released and a regional store is provisioned for acceptance.

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 14, §4 Phase 4.3, Appendix A.5.3 and
Appendix A.13, plus `docs/anncsu-addresses.md` in the sibling package. The PDF
comparison remains in Appendix A.5.8; its Zornade side stays explicitly
unverified until an exported sample for the same parcel is inspected.
