from land_registry.map_preflight import evaluate_health, run_preflight


def _health(*, coverage="full", available=True):
    return [{
        "id": "example",
        "available": available,
        "relation_exists": available,
        "geometry_column_exists": available,
        "gist_index_exists": available,
        "expected_srid": 4326,
        "geometry_srid": 4326 if available else 0,
        "srid_matches": available,
        "coverage": coverage,
        "row_estimate": 10,
    }]


class _Source:
    def __init__(self, available=True, health=None):
        self.available = available
        self._health = health or _health(available=available)
        self.closed = False

    def health(self):
        return self._health

    def close(self):
        self.closed = True


def test_preflight_blocks_disabled_source():
    assert evaluate_health(_health(available=False), source_available=False)


def test_preflight_blocks_partial_coverage_by_default():
    problems = evaluate_health(_health(coverage="partial"), source_available=True)
    assert problems == ["example: coverage is partial"]


def test_preflight_can_allow_known_partial_development_data():
    assert evaluate_health(_health(coverage="partial"), source_available=True, allow_partial=True) == []


def test_run_preflight_closes_source():
    source = _Source()
    health, problems = run_preflight(source_factory=lambda: source)
    assert health and not problems
    assert source.closed
