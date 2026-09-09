from land_registry import main


def test_port_probe_is_fail_safe_when_socket_creation_is_denied(monkeypatch):
    class DeniedSocket:
        def __init__(self, *args, **kwargs):
            raise PermissionError("socket denied")

    monkeypatch.setattr("socket.socket", DeniedSocket)

    assert main._is_port_in_use("127.0.0.1", 5006) is False
