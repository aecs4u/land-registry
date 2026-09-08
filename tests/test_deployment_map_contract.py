from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cloud_run_injects_and_verifies_canonical_map_database():
    workflow = (ROOT / ".github/workflows/deploy-cloudrun.yml").read_text(encoding="utf-8")
    assert "AECS4U_STATS_POSTGRES_ENABLE=1" in workflow
    assert "AECS4U_STATS_POSTGRES_DSN=AECS4U_STATS_POSTGRES_DSN:latest" in workflow
    assert "/api/v1/map/layers/health" in workflow
    assert "len(layers) == 15" in workflow
