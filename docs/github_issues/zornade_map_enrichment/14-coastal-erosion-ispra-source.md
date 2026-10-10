## Summary

Add an upstream, cache-backed query for ISPRA's national shoreline-change
layer so the parcel panel can show mapped coastal change within 1 km of a
parcel.

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

Proposed query: `coastal_changes_near_geometry(geometry, radius_m=1000,
limit=5)`. Return only validated source attributes, segment geometry or a
generalized representation, parcel-to-segment distance, source release,
spatial accuracy and a completeness state. Keep the upstream FeatureServer
outside parcel request handling: import or cache the vector data in the shared
spatial store and query it locally with a bounded candidate limit.

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

- Tests cover parcel geometry with an erosion segment, with only a stable or
  advancing segment, with no mapped segment, and with an unavailable store.
- Candidate caps produce an explicit partial result.
- The returned metadata names ISPRA, the 2006–2020 period, CC BY 4.0, 5 m
  positional accuracy and the 5 m classification threshold.
- The panel keeps the section separate from ISPRA flood/landslide hazards and
  does not label the historic line as a current forecast.
- The PDF source register carries the ISPRA citation and licence.

## Dependencies

- Upstream `aecs4u_stats` ingestion, local query function and source contract.
- Validate how the Google imagery lineage relates to redistribution of the
  ISPRA vector product before publishing derived parcel reports.

## References

- [ISPRA coastal-dynamics indicator](https://indicatoriambientali.isprambiente.it/it/pon/linea-1-linea-3/dinamica-litoranea-erosione-e-avanzamento)
- [ISPRA shoreline-change layer](https://sinacloud.isprambiente.it/arcgisadv/rest/services/coste/Assetto_costiero_anno_2020/FeatureServer/1)
- [RNDT metadata, Linea di Costa 2020 v2.0](https://geodati.gov.it/geoportale/visualizzazione-metadati/scheda-metadati?metadataid=ispra_rm%3A01Lineacosta2020_v2.0_DT)
