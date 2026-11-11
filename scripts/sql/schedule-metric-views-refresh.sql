-- Cloud SQL / any server with pg_cron: refresh the map metric views daily.
-- Run in the database where pg_cron is installed (cron.database_name, usually `postgres`), as a role
-- allowed to use pg_cron. The job runs in the istat database, which owns lr_map.
--
-- Local development uses crontab instead:
--   40 3 * * * cd <repo> && flock -n /tmp/land-registry-metric-views.lock python -m land_registry.metric_views
SELECT cron.schedule_in_database(
    'land-registry-metric-views-refresh',
    '40 3 * * *',
    $$SELECT lr_map.refresh_metric_views()$$,
    'istat'
);
-- Remove:  SELECT cron.unschedule('land-registry-metric-views-refresh');
