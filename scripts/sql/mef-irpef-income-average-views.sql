-- Build taxpayer-weighted average taxable income views in the MEF source DB.
-- (The aecs4u-stats foreign tables that used to expose them are gone: consumers read this database directly.)
--
-- NOTE: the live mef_irpef database keeps these views in the schema `mef_irpef`; this script was written for
-- `public`. Adjust the schema before re-running it.
--
--   psql -X -U postgres -v reader_role=<role> -f scripts/sql/mef-irpef-income-average-views.sql

\set ON_ERROR_STOP on
\if :{?reader_role}
\else
\echo 'Set -v reader_role=<database-role> to the application reader role.'
\quit 2
\endif

\connect mef_irpef

CREATE OR REPLACE VIEW public.income_average_comune AS
SELECT anno,
       cod_catastale::text AS codice,
       comune::text AS nome,
       provincia::text AS provincia,
       regione::text AS regione,
       SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                THEN imponibile_freq END)::numeric AS imponibile_freq,
       SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                THEN imponibile_amount END)::numeric AS imponibile_amount,
       ROUND(
           SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                    THEN imponibile_amount END)::numeric
           / NULLIF(SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                             THEN imponibile_freq END)::numeric, 0),
           2
       ) AS average_income_eur
  FROM public.irpef_comuni
 GROUP BY anno, cod_catastale, comune, provincia, regione;

CREATE OR REPLACE VIEW public.income_average_provincia AS
SELECT anno,
       provincia::text AS codice,
       provincia::text AS nome,
       provincia::text AS provincia,
       regione::text AS regione,
       SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                THEN imponibile_freq END)::numeric AS imponibile_freq,
       SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                THEN imponibile_amount END)::numeric AS imponibile_amount,
       ROUND(
           SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                    THEN imponibile_amount END)::numeric
           / NULLIF(SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                             THEN imponibile_freq END)::numeric, 0),
           2
       ) AS average_income_eur
  FROM public.irpef_comuni
 GROUP BY anno, provincia, regione;

CREATE OR REPLACE VIEW public.income_average_regione AS
SELECT anno,
       cod_istat_regione::text AS codice,
       regione::text AS nome,
       NULL::text AS provincia,
       regione::text AS regione,
       SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                THEN imponibile_freq END)::numeric AS imponibile_freq,
       SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                THEN imponibile_amount END)::numeric AS imponibile_amount,
       ROUND(
           SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                    THEN imponibile_amount END)::numeric
           / NULLIF(SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                             THEN imponibile_freq END)::numeric, 0),
           2
       ) AS average_income_eur
  FROM public.irpef_comuni
 GROUP BY anno, cod_istat_regione, regione;

CREATE OR REPLACE VIEW public.income_average_italia AS
SELECT anno,
       'IT'::text AS codice,
       'Italia'::text AS nome,
       NULL::text AS provincia,
       NULL::text AS regione,
       SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                THEN imponibile_freq END)::numeric AS imponibile_freq,
       SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                THEN imponibile_amount END)::numeric AS imponibile_amount,
       ROUND(
           SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                    THEN imponibile_amount END)::numeric
           / NULLIF(SUM(CASE WHEN imponibile_amount IS NOT NULL AND imponibile_freq IS NOT NULL
                             THEN imponibile_freq END)::numeric, 0),
           2
       ) AS average_income_eur
  FROM public.irpef_comuni
 GROUP BY anno;

GRANT SELECT ON public.income_average_comune,
                public.income_average_provincia,
                public.income_average_regione,
                public.income_average_italia
   TO :"reader_role";
