"""_detect_advertise_ip: explicit setting wins; otherwise the UDP-connect
trick reports the primary outbound IP (no packets sent).
"""

from allocator_agent import main as agent_main


def test_advertise_ip_setting_wins(monkeypatch):
    monkeypatch.setattr(agent_main.settings, "advertise_ip", "10.9.8.7")
    assert agent_main._detect_advertise_ip() == "10.9.8.7"


def test_advertise_ip_udp_trick(monkeypatch):
    monkeypatch.setattr(agent_main.settings, "advertise_ip", "")

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def connect(self, addr):
            assert addr == ("8.8.8.8", 80)

        def getsockname(self):
            return ("192.168.1.42", 0)

    monkeypatch.setattr(agent_main.socket, "socket", lambda *a, **k: FakeSock())
    assert agent_main._detect_advertise_ip() == "192.168.1.42"
