"""Process-isolated worker for optional OpenData/PVP/SISTER PostgreSQL reads."""

import json
import sys

source_name = "external"


def main() -> int:
    global source_name
    request = json.load(sys.stdin)
    source_name = request.get("source")
    if source_name == "OpenData":
        from land_registry.stats_service import _OpenDataPostgresSource as source_type
    elif source_name == "PVP":
        from land_registry.stats_service import _PvpPostgresSource as source_type
    elif source_name == "Sister":
        from land_registry.stats_service import _SisterPostgresSource as source_type
    else:
        raise ValueError(f"unknown external PostgreSQL source: {source_name!r}")

    source = source_type.from_dsn(request.get("dsn"))
    if source is None:
        result = {"records": [], "count": 0, "available": False, "source": f"{source_name} PostgreSQL"}
    else:
        result = source.parcel_data(
            request.get("national_reference", ""),
            request.get("municipality"),
        )
    json.dump(result, sys.stdout, default=str)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # return structured failure without a traceback
        json.dump({
            "records": [],
            "count": 0,
            "available": False,
            "source": f"{source_name} PostgreSQL",
            "error": str(exc) or type(exc).__name__,
        }, sys.stdout)
        raise SystemExit(0)
