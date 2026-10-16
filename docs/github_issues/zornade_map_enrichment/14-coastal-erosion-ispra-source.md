## Summary

Add an upstream, cache-backed query for ISPRA's national shoreline-change
layer so the parcel panel can show mapped coastal change within 1 km of a
parcel.

**Implementation status (2026-10-15):** the application has a local PostGIS
snapshot schema, a dry-run-first/atomic refresh script, an indexed 1 km parcel
query, `/api/v1/enrichment/parcel/coastal-erosion/{reference}`, the lazy
parcel-panel section, `/api/v2` standalone and risk components, and Typst
dossier source-register attribution. The configured stats database was
provisioned from the complete ISPRA object-ID list: 99,229 features across 664
ISTAT municipalities. Two real Civitavecchia parcels returned 35 and 67 mapped
segments; a dense-area query returned the explicit 100-candidate partial state.
Target stats databases still need the same schema and snapshot, and provider
reuse review remains open.

## Source and contract

- Dataset: ISPRA `Linea di Costa 2020 v2.0`.
- Layer: `Dinamica_Litoranea_2006_2020` from the `Assetto costiero anno 2020`
  FeatureServer.
- Classification: retreat over 5 m from 2006 to 2020 is `Erosione`, seaward
  advance over 5 m is `Avanzamento`, and changes within ±5 m are `Stabilità`.
- Licence: CC BY 4.0 in the RNDT dataset metadata. Preserve the ISPRA citation
  and do not redistribute the imagery used as an input to digitization.
- The 2020 characterization uses mostly Google Maps imagery from 2017–2020;
  this is a historical change layer, not a current erosion forecast.

Query: the refresh script reads the FeatureServer outside parcel request
handling and stores validated line geometry and selected source attributes in
`spatial.ispra_coastal_dynamics`. The app queries the shared local PostGIS
snapshot with `ST_DWithin`, a 1 km radius and a 100-candidate cap. It reports
parcel-to-segment distances, source release, positional accuracy and whether
the local query reached its cap.

## Coverage semantics

- A matched erosion segment reports the ISPRA classification and observation
  period `2006–2020`; it must not be described as current erosion.
- Advance, stability and unknown-information classes remain distinct from
  erosion.
- No segment within the search radius means “no mapped shoreline-change
  segment found”; it does not establish that a parcel is unaffected by erosion.
- An absent store and a failed query return separate unavailable and temporary
  failure states.
- The source's documented positional accuracy is 5 m. Return the query radius,
  match distance, source version and coverage notes.

## Acceptance criteria

- Tests cover parcel geometry with erosion, stable and advancing segments,
  unknown coverage, an unavailable store and candidate-cap partial results
  (`tests/test_coastal_erosion.py`; 6 passed).
- Candidate caps produce an explicit partial result. A dense-area query on the
  configured stats database returned 100 candidates with `coverage_status:
  partial` and `query_complete: false`.
- The returned metadata names ISPRA, the 2006–2020 period, CC BY 4.0, 5 m
  positional accuracy and the 5 m classification threshold. *(Implemented.)*
- The panel keeps the section separate from ISPRA flood/landslide hazards and
  does not label the historic line as a current forecast. *(Implemented.)*
- The PDF source register carries the ISPRA citation and licence. *(Implemented.)*

## Dependencies

- Provision and refresh the local PostGIS table on each target stats database;
  verify status, parcel coverage and no-match/candidate-cap behavior there. The
  configured database has the complete 99,229-feature source snapshot, and the
  application reader has successfully queried it.
- Validate how the Google imagery lineage relates to redistribution of the
  ISPRA vector product before publishing derived parcel reports.

## References

- [ISPRA coastal-dynamics indicator](https://indicatoriambientali.isprambiente.it/it/pon/linea-1-linea-3/dinamica-litoranea-erosione-e-avanzamento)
- [ISPRA shoreline-change layer](https://sinacloud.isprambiente.it/arcgisadv/rest/services/coste/Assetto_costiero_anno_2020/FeatureServer/1)
- [RNDT metadata, Linea di Costa 2020 v2.0](https://geodati.gov.it/geoportale/visualizzazione-metadati/scheda-metadati?metadataid=ispra_rm%3A01Lineacosta2020_v2.0_DT)
