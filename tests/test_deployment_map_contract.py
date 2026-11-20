from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cloud_run_injects_and_verifies_canonical_map_database():
    workflow = (ROOT / ".github/workflows/deploy-cloudrun.yml").read_text(encoding="utf-8")
    assert "STATS_POSTGRES_ENABLE=1" in workflow
    assert workflow.count("STATS_POSTGRES_SOURCE_FALLBACK_ENABLE") == 1
    assert "--remove-env-vars=\"DEBUG,LOG_LEVEL,AUTH_MODE,NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,STATS_POSTGRES_SOURCE_FALLBACK_ENABLE\"" in workflow
    assert "STATS_POSTGRES_DSN=STATS_POSTGRES_DSN:latest" not in workflow
    assert "CADASTRAL_POSTGRES_DSN=LAND_REGISTRY_CADASTRAL_POSTGRES_DSN:latest" in workflow
    assert '--update-env-vars="' in workflow
    assert '--update-secrets="$SECRET_UPDATES"' in workflow
    assert "/api/v1/map/layers/health" in workflow
    # The map database is optional at deploy time (784ba73): a database outage
    # must not fail or roll back a release, so readiness is reported as a
    # warning and the health step no longer asserts on layer availability.
    assert "/ready" in workflow
    assert "::warning::" in workflow
    assert 'assert d.get("available")' not in workflow
