# DPC bulletins in hazards PostgreSQL

The Civil Protection overlay and saved-parcel hazard checks read
`hazards.public.dpc_criticality_bulletin`. No request to GitHub runs in either
request path. The table holds one complete JSONB snapshot per bulletin stamp,
including metadata and both available days of TopoJSON, plus the fetch timestamp.

Set `HAZARDS_POSTGRES_DSN` or `AECS4U_STATS_HAZARDS_DATABASE_URL` to a PostgreSQL
URL whose database is `hazards`. If neither is set, the application uses the
`STATS_POSTGRES_DSN` (or `AECS4U_STATS_POSTGRES_DSN`) server and credentials with
the database changed to `hazards`. It never uses the application `DATABASE_URL`.

Initialize and populate the table with a role allowed to create tables in
`hazards.public`:

```sh
.venv/bin/python -m land_registry.bulletin_store --initialize
```

Run the refresh separately every 15 minutes, for example from cron. Replace
the working directory with the deployed checkout path:

```cron
*/15 * * * * cd /mnt/mobile/git/aecs4u.it/land-registry && /usr/bin/flock -n /tmp/land-registry-dpc-refresh.lock .venv/bin/python -m land_registry.bulletin_store >> /tmp/land-registry-dpc-refresh.log 2>&1
```

The CLI loads the checkout's `.env`. A refresh reuses one HTTP connection pool,
validates the complete snapshot before writing, and atomically upserts by
repository and bulletin stamp. Failure exits nonzero and retains previous data.
Repeated runs can update a corrected bulletin without introducing duplicates.
The lock prevents overlapping runs.

For separate writer/reader roles, grant the web role `SELECT` on
`public.dpc_criticality_bulletin`; grant the job role `SELECT, INSERT, UPDATE`.
Configure each process with its respective hazards DSN. Map reads use a read-only
connection with connection and statement timeouts.

The query transfers only the applicable day's geometry, and the API omits the
other day's geometry to keep the map response small. Both remain in PostgreSQL.

Dates use Europe/Rome. After midnight, yesterday's tomorrow geometry becomes
today's geometry. Once neither stored day covers today, the API marks the
snapshot stale and omits today's geometry; the UI reports expired data.
An empty or unavailable store returns HTTP 503 and never downloads synchronously.
