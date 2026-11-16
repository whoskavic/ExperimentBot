import asyncio
import threading
from types import SimpleNamespace

import pytest

import web.server as server


@pytest.fixture
def fake_discord(monkeypatch):
    """Event loop di thread terpisah, seperti discord.py saat bot berjalan."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    monkeypatch.setattr(server, "_discord_client", SimpleNamespace(loop=loop, get_channel=lambda _id: "dest"))
    yield
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)
    loop.close()


def test_post_recap_includes_save_report(auth_client, fake_discord, monkeypatch):
    calls = {}
    report = [{"source_id": "800000000000000001", "source": "channel a", "kind": "channel",
               "dates": [{"recap_date": "2026-11-12", "status": "created", "recap_id": 1,
                          "revision_no": None, "names_updated": 0, "error": None}]}]

    async def fake_run_all(client, date_from, date_to, triggered_by="panel"):
        calls["run"] = (str(date_from), str(date_to), triggered_by)
        return [("recap_2026-11-12_channel_a.xlsx", "src")], report

    async def fake_send(dest, results, date_from, date_to):
        calls["send"] = (dest, results)
        return ["recap_2026-11-12_channel_a.xlsx", "recap_2026-11-12_all_channel.xlsx"]

    monkeypatch.setattr(server, "run_all_recaps", fake_run_all)
    monkeypatch.setattr(server, "send_recaps", fake_send)

    res = auth_client.post("/recap", json={"date_from": "2026-11-12", "date_to": "2026-11-12"},
                           headers={"X-CSRFToken": auth_client.csrf})
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["files"] == ["recap_2026-11-12_channel_a.xlsx", "recap_2026-11-12_all_channel.xlsx"]
    assert body["saved"] == report
    assert calls["run"] == ("2026-11-12", "2026-11-12", "panel:admin")
    assert calls["send"][0] == "dest"


def test_post_recap_no_sources_returns_empty(auth_client, fake_discord, monkeypatch):
    async def fake_run_all(*a, **kw):
        return [], []
    monkeypatch.setattr(server, "run_all_recaps", fake_run_all)

    body = auth_client.post("/recap", json={}, headers={"X-CSRFToken": auth_client.csrf}).get_json()
    assert (body["status"], body["files"], body["saved"]) == ("ok", [], [])


def test_post_recap_validation_unchanged(auth_client):
    h = {"X-CSRFToken": auth_client.csrf}
    r = auth_client.post("/recap", json={"date_from": "12-11-2026"}, headers=h)
    assert r.status_code == 400 and "Format tanggal" in r.get_json()["message"]
    r = auth_client.post("/recap", json={"date_from": "2026-11-13", "date_to": "2026-11-12"}, headers=h)
    assert r.status_code == 400 and "date_from" in r.get_json()["message"]
