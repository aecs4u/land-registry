# Land Registry CLI

The project ships with a unified CLI entrypoint:

```bash
land-registry --help
```

If the script is not installed in your shell yet, use:

```bash
.venv/bin/python -m land_registry --help
```

## Commands

### `serve`
Run the FastAPI app with Uvicorn.

```bash
land-registry serve [--target main] [--host HOST] [--port PORT] [--reload|--no-reload] [--log-level LEVEL]
```

Examples:

```bash
land-registry serve --reload
land-registry serve --host 0.0.0.0 --port 8000 --log-level info
```

### `info`
Print effective runtime/configuration values.

```bash
land-registry info [--json]
```

Examples:

```bash
land-registry info
land-registry info --json
```

### `routes`
List registered FastAPI routes.

```bash
land-registry routes [--target main] [--json]
```

Examples:

```bash
land-registry routes
land-registry routes --json
```

### `cadastral-stats`
Show cadastral structure statistics and cache metadata.

```bash
land-registry cadastral-stats [--fresh] [--json]
```

Examples:

```bash
land-registry cadastral-stats
land-registry cadastral-stats --fresh --json
```

### `cache show`
Show file availability cache database stats.

```bash
land-registry cache show [--json]
```

### `cache clear`
Clear runtime caches.

```bash
land-registry cache clear [--all] [--cadastral] [--file-db]
```

Behavior:
- No flags clears both caches.
- `--cadastral` clears in-memory cadastral cache.
- `--file-db` clears file availability cache DB entries.
- `--all` clears both.

## Global Options

```bash
land-registry --help
land-registry --version
```

## Exit Codes

- `0`: success
- `1`: runtime error
- `130`: interrupted (`Ctrl+C`)

## Typical Workflow

```bash
# 1) Check effective environment/config
land-registry info --json

# 2) Start app
land-registry serve --reload

# 3) Inspect routes while developing
land-registry routes

# 4) Inspect or reset caches
land-registry cache show
land-registry cache clear --all
```
