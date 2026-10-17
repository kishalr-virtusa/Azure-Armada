"""Email delivery chain: retries, fallback order, never raises."""
from dr_agent.tools import notify as n


def test_falls_back_to_next_channel(monkeypatch):
    calls = []
    monkeypatch.setattr(n, "RETRY_DELAYS_S", (0, 0, 0))
    monkeypatch.setattr(n, "email_channels", lambda: ["resend", "azure-monitor"])
    monkeypatch.setattr(n, "_record", lambda *a: None)

    def bad(*a):
        calls.append("resend")
        raise RuntimeError("HTTP 500")
    monkeypatch.setattr(n, "_send_via_resend", bad)
    monkeypatch.setattr(n, "_send_via_azure_monitor", lambda *a: calls.append("monitor"))
    out = n.deliver_email("action", "t", "b")
    assert out["email_sent"] and out["email_channel"] == "azure-monitor"
    assert calls == ["resend"] * 3 + ["monitor"] and "HTTP 500" in out["email_errors"]


def test_retry_then_success(monkeypatch):
    attempts = {"n": 0}
    monkeypatch.setattr(n, "RETRY_DELAYS_S", (0, 0, 0))
    monkeypatch.setattr(n, "email_channels", lambda: ["resend"])
    monkeypatch.setattr(n, "_record", lambda *a: None)

    def flaky(*a):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise TimeoutError("slow")
    monkeypatch.setattr(n, "_send_via_resend", flaky)
    out = n.deliver_email("success", "t", "b")
    assert out["email_sent"] and attempts["n"] == 2


def test_all_channels_down_never_raises(monkeypatch):
    monkeypatch.setattr(n, "RETRY_DELAYS_S", (0,))
    monkeypatch.setattr(n, "email_channels", lambda: ["resend"])
    monkeypatch.setattr(n, "_record", lambda *a: None)
    monkeypatch.setattr(n, "_send_via_resend", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    assert n.deliver_email("failure", "t", "b")["email_sent"] is False
