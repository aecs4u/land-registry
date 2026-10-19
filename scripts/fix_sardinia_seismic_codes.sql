-- Align Sardinian municipality codes in hazards.public.seismic_classification with the
-- current ISTAT codes used by istat_boundaries_fdw.v_comuni_map (Sardinia's 2016-2021
-- province reform changed them, e.g. Sarroch 92066 -> 118047). Without this the
-- seismic map layer draws no Sardinian municipalities and the per-parcel seismic lookup
-- returns nothing for Sardinian parcels.
--
--   psql "host=127.0.0.1 dbname=hazards user=postgres" -v ON_ERROR_STOP=1 \
--        -f scripts/fix_sardinia_seismic_codes.sql
--
-- Matches by municipality name (all 377 rows match exactly one boundary). Idempotent:
-- rows whose code already exists in the boundaries are not touched, and it aborts
-- unless exactly 377 distinct codes are mapped.
-- Not covered: Nanto and Castegnero (VI) and Lirio (PV) were merged into other
-- municipalities and have no boundary of their own.
BEGIN;

CREATE TEMP TABLE sardinia_code_map AS
SELECT s.istat_code AS old_code, b.pro_com AS new_code
FROM public.seismic_classification s
JOIN istat_boundaries_fdw.v_comuni_map b
  ON lower(b.name) = lower(s.comune) AND b.pro_com BETWEEN 90000 AND 125000
WHERE s.regione ILIKE 'sardegna'
  AND NOT EXISTS (SELECT 1 FROM istat_boundaries_fdw.v_comuni_map x WHERE x.pro_com = s.istat_code);

DO $$
DECLARE n bigint; d bigint;
BEGIN
  SELECT count(*), count(DISTINCT new_code) INTO n, d FROM sardinia_code_map;
  IF n = 0 THEN
    RAISE NOTICE 'nothing to do';
  ELSIF n <> 377 OR d <> 377 THEN
    RAISE EXCEPTION 'unexpected mapping size % (% distinct); not updating', n, d;
  END IF;
END $$;

UPDATE public.seismic_classification s
SET istat_code = m.new_code
FROM sardinia_code_map m
WHERE s.istat_code = m.old_code AND s.regione ILIKE 'sardegna';

SELECT count(*) AS still_unmatched_in_sardinia
FROM public.seismic_classification s
WHERE s.regione ILIKE 'sardegna'
  AND NOT EXISTS (SELECT 1 FROM istat_boundaries_fdw.v_comuni_map b WHERE b.pro_com = s.istat_code);

COMMIT;
