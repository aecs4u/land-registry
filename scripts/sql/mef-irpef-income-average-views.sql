-- Build taxpayer-weighted average taxable income views in the MEF source DB,
-- then expose them to aecs4u-stats through its existing mef_irpef FDW server.
--
-- Run from aecs4u-stats as a role with access to both databases:
--   psql -X -U postgres -d aecs4u-stats -v stats_reader_role=<role> \
--     -f scripts/sql/mef-irpef-income-average-views.sql

\set ON_ERROR_STOP on
\if :{?stats_reader_role}
\else
\echo 'Set -v stats_reader_role=<database-role> to the Stats application reader role.'
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
   TO :"stats_reader_role";

\connect aecs4u-stats

CREATE SCHEMA IF NOT EXISTS mef_irpef;

CREATE FOREIGN TABLE IF NOT EXISTS mef_irpef.income_average_comune (
    anno bigint,
    codice text,
    nome text,
    provincia text,
    regione text,
    imponibile_freq numeric,
    imponibile_amount numeric,
    average_income_eur numeric
) SERVER mef_irpef_source
  OPTIONS (schema_name 'public', table_name 'income_average_comune');

CREATE FOREIGN TABLE IF NOT EXISTS mef_irpef.income_average_provincia (
    anno bigint,
    codice text,
    nome text,
    provincia text,
    regione text,
    imponibile_freq numeric,
    imponibile_amount numeric,
    average_income_eur numeric
) SERVER mef_irpef_source
  OPTIONS (schema_name 'public', table_name 'income_average_provincia');

CREATE FOREIGN TABLE IF NOT EXISTS mef_irpef.income_average_regione (
    anno bigint,
    codice text,
    nome text,
    provincia text,
    regione text,
    imponibile_freq numeric,
    imponibile_amount numeric,
    average_income_eur numeric
) SERVER mef_irpef_source
  OPTIONS (schema_name 'public', table_name 'income_average_regione');

CREATE FOREIGN TABLE IF NOT EXISTS mef_irpef.income_average_italia (
    anno bigint,
    codice text,
    nome text,
    provincia text,
    regione text,
    imponibile_freq numeric,
    imponibile_amount numeric,
    average_income_eur numeric
) SERVER mef_irpef_source
  OPTIONS (schema_name 'public', table_name 'income_average_italia');

GRANT USAGE ON SCHEMA mef_irpef TO :"stats_reader_role";
GRANT SELECT ON mef_irpef.income_average_comune,
                mef_irpef.income_average_provincia,
                mef_irpef.income_average_regione,
                mef_irpef.income_average_italia
   TO :"stats_reader_role";
