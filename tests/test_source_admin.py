"""Langkah 8: pemetaan source → tim (service + CLI + filter riwayat). Data fiktif."""
from datetime import date

import pytest
from sqlalchemy import select

import config
from db.base import session_scope
from db.models import Source
from services.recap_store import EntryIn, SourceInfo, save_recap_day
from services.source_admin import (
    PLACEHOLDER_NAME, TEAMS, SourceAdminError, clear_team, list_sources, normalize_team, set_team,
)

TODAY = date(2026, 11, 15)
CH_RECAPPED = 800000000000000001     # di config, sudah pernah direkap
CH_NEW      = 800000000000000002     # di config, belum pernah direkap
TH_NEW      = 800000000000000003     # thread di config, belum pernah direkap
OLD_SOURCE  = 800000000000000009     # ada di DB, sudah dihapus dari config
NOT_CONFIG  = 800000000000000099     # tidak ada di mana pun
U1 = 900000000000000001


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_IDS", [CH_RECAPPED, CH_NEW])
    monkeypatch.setattr(config, "THREAD_IDS", [TH_NEW])


@pytest.fixture
def seeded(db, cfg):
    save_recap_day(SourceInfo(CH_RECAPPED, "channel", "opening-closing"), date(2026, 11, 12),
                   [EntryIn(U1, "ANDI", opening=True)], "scheduler", TODAY)
    save_recap_day(SourceInfo(OLD_SOURCE, "channel", "channel lama"), date(2026, 11, 12), [], "scheduler", TODAY)


def get_source(discord_id):
    with session_scope() as s:
        src = s.scalars(select(Source).where(Source.discord_id == discord_id)).one_or_none()
        return None if src is None else (src.kind, src.name, src.team)


# ── normalize_team ────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("Produk", "Produk"), ("produk", "Produk"), ("  PRODUK ", "Produk"),
    ("implementor", "Implementor"), ("DEVELOPMENT", "Development"), ("dEvElOpMeNt", "Development"),
])
def test_normalize_team(raw, expected):
    assert normalize_team(raw) == expected


@pytest.mark.parametrize("raw", ["Marketing", "", "  ", "prod", "Produk Tim", None])
def test_normalize_team_rejects_others(raw):
    with pytest.raises(SourceAdminError) as exc:
        normalize_team(raw)
    assert "Produk, Implementor, Development" in str(exc.value)


# ── set_team ──────────────────────────────────────────────

def test_set_team_existing_source(seeded):
    ch = set_team(CH_RECAPPED, "produk")
    assert (ch.old_team, ch.new_team, ch.created, ch.name) == (None, "Produk", False, "opening-closing")
    assert get_source(CH_RECAPPED) == ("channel", "opening-closing", "Produk")

    ch = set_team(CH_RECAPPED, "DEVELOPMENT")
    assert (ch.old_team, ch.new_team) == ("Produk", "Development")


@pytest.mark.parametrize("discord_id, kind", [(CH_NEW, "channel"), (TH_NEW, "thread")])
def test_set_team_source_never_recapped_uses_config_kind(seeded, discord_id, kind):
    ch = set_team(discord_id, "Implementor")
    assert ch.created is True and ch.name == PLACEHOLDER_NAME
    assert get_source(discord_id) == (kind, PLACEHOLDER_NAME, "Implementor")


def test_placeholder_name_replaced_on_next_recap_team_kept(seeded):
    set_team(TH_NEW, "Development")
    save_recap_day(SourceInfo(TH_NEW, "thread", "thread dev"), date(2026, 11, 13), [], "scheduler", TODAY)
    assert get_source(TH_NEW) == ("thread", "thread dev", "Development")


def test_set_team_rejects_id_not_in_config(seeded):
    with pytest.raises(SourceAdminError, match="tidak ada di channel_ids / thread_ids"):
        set_team(NOT_CONFIG, "Produk")
    with pytest.raises(SourceAdminError, match="tidak ada di channel_ids / thread_ids"):
        set_team(OLD_SOURCE, "Produk")          # ada di DB, tapi sudah tidak di config
    assert get_source(NOT_CONFIG) is None
    assert get_source(OLD_SOURCE)[2] is None


def test_set_team_invalid_team_changes_nothing(seeded):
    with pytest.raises(SourceAdminError, match="Tim tidak dikenal"):
        set_team(CH_NEW, "Marketing")
    assert get_source(CH_NEW) is None


# ── clear_team ────────────────────────────────────────────

def test_clear_team(seeded):
    set_team(CH_RECAPPED, "Produk")
    ch = clear_team(CH_RECAPPED)
    assert (ch.old_team, ch.new_team) == ("Produk", None)
    assert get_source(CH_RECAPPED)[2] is None


def test_clear_team_source_removed_from_config_allowed(seeded):
    with session_scope(write=True) as s:
        s.scalars(select(Source).where(Source.discord_id == OLD_SOURCE)).one().team = "Produk"
    assert clear_team(OLD_SOURCE).old_team == "Produk"


def test_clear_team_never_recapped_in_config(seeded):
    ch = clear_team(CH_NEW)
    assert ch.old_team is None and get_source(CH_NEW) is None      # tidak membuat baris baru


def test_clear_team_unknown_id(seeded):
    with pytest.raises(SourceAdminError, match="tidak ditemukan"):
        clear_team(NOT_CONFIG)


# ── list_sources ──────────────────────────────────────────

def test_list_sources_union_of_config_and_db(seeded):
    set_team(CH_RECAPPED, "Produk")
    rows = [(r.discord_id, r.kind, r.name, r.team, r.in_config, r.in_db) for r in list_sources()]
    assert rows == [
        (CH_NEW, "channel", PLACEHOLDER_NAME, None, True, False),
        (TH_NEW, "thread", PLACEHOLDER_NAME, None, True, False),
        (CH_RECAPPED, "channel", "opening-closing", "Produk", True, True),
        (OLD_SOURCE, "channel", "channel lama", None, False, True),     # yang tidak di config paling bawah
    ]


# ── CLI ───────────────────────────────────────────────────

def run(app, *args):
    return app.test_cli_runner().invoke(args=[str(a) for a in args])


def test_cli_list_sources(app, seeded):
    set_team(CH_RECAPPED, "Produk")
    res = run(app, "list-sources")
    assert res.exit_code == 0, res.output
    lines = res.output.splitlines()
    assert lines[0].split() == ["DISCORD_ID", "KIND", "NAME", "TEAM", "STATUS"]
    assert any(str(CH_RECAPPED) in l and "opening-closing" in l and "Produk" in l and l.endswith("ok") for l in lines)
    assert any(str(CH_NEW) in l and "belum direkap" in l for l in lines)
    assert any(str(OLD_SOURCE) in l and "tidak ada di config" in l for l in lines)
    assert "Produk, Implementor, Development" in res.output


def test_cli_list_sources_empty(app, db, monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_IDS", [])
    monkeypatch.setattr(config, "THREAD_IDS", [])
    res = run(app, "list-sources")
    assert res.exit_code == 0 and "Belum ada source" in res.output


def test_cli_set_and_clear_team(app, seeded):
    res = run(app, "set-team", CH_NEW, "implementor")
    assert res.exit_code == 0, res.output
    assert "- → Implementor" in res.output and "belum pernah direkap" in res.output

    res = run(app, "set-team", CH_RECAPPED, "produk")
    assert res.exit_code == 0 and "opening-closing: - → Produk" in res.output

    res = run(app, "clear-team", CH_RECAPPED)
    assert res.exit_code == 0 and "Produk → -" in res.output
    res = run(app, "clear-team", CH_RECAPPED)
    assert res.exit_code == 0 and "memang belum punya tim" in res.output


@pytest.mark.parametrize("args, msg", [
    (("set-team", CH_NEW, "Marketing"), "Tim tidak dikenal"),
    (("set-team", NOT_CONFIG, "Produk"), "tidak ada di channel_ids"),
    (("set-team", "bukan-angka", "Produk"), "is not a valid integer"),
    (("set-team", CH_NEW), "Missing argument"),
    (("clear-team", NOT_CONFIG), "tidak ditemukan"),
])
def test_cli_errors(app, seeded, args, msg):
    res = run(app, *args)
    assert res.exit_code != 0
    assert msg in res.output


# ── filter tim di riwayat langsung bekerja ────────────────

def test_history_team_filter_after_mapping(auth_client, seeded):
    def total(team):
        return auth_client.get("/api/recaps", query_string={
            "date_from": "2026-11-01", "date_to": "2026-11-15", "team": team}).get_json()["total"]

    assert total("Produk") == 0
    set_team(CH_RECAPPED, "produk")
    assert total("Produk") == 1
    assert total("produk") == 1                 # filter juga case-insensitive
    assert total("__none__") == 1               # channel lama (tanpa tim)

    body = auth_client.get("/api/sources").get_json()
    assert body["teams"] == list(TEAMS)
    assert {s["name"]: s["team"] for s in body["sources"]}["opening-closing"] == "Produk"

    clear_team(CH_RECAPPED)
    assert total("Produk") == 0 and total("__none__") == 2


def test_export_team_filter_after_mapping(auth_client, seeded):
    import openpyxl
    from io import BytesIO
    set_team(CH_RECAPPED, "Produk")
    res = auth_client.get("/api/recaps/export", query_string={"date_from": "2026-11-12", "team": "Produk"})
    rows = [list(r) for r in openpyxl.load_workbook(BytesIO(res.data))["Recap"].iter_rows(values_only=True)]
    assert rows[1:] == [["2026-11-12", "opening-closing", "ANDI", 1, 0, 0, 0]]
