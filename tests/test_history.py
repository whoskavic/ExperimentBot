"""Langkah 7: riwayat rekap, detail revisi, export Excel dari DB. Data fiktif."""
from datetime import date, datetime
from io import BytesIO

import openpyxl
import pytest
from sqlalchemy import select

import services.recap as recap
from db.base import session_scope
from db.models import Recap, Source
from services.recap_query import (
    MAX_RANGE_DAYS, XLSX_HEADER, FilterError, export_xlsx, parse_filter, to_wib, RecapFilter,
)
from services.recap_store import EntryIn, SourceInfo, save_recap_day

TODAY = date(2026, 11, 15)
SRC_A = SourceInfo(800000000000000001, "channel", "opening-closing")
SRC_B = SourceInfo(800000000000000002, "thread", "thread dev")
U1, U2, U3 = 900000000000000001, 900000000000000002, 900000000000000003


def e(uid, name, **flags):
    return EntryIn(discord_user_id=uid, display_name=name, **flags)


def set_team(discord_id, team):
    with session_scope(write=True) as s:
        s.scalars(select(Source).where(Source.discord_id == discord_id)).one().team = team


def set_updated_at(source: SourceInfo, d: date, utc: datetime):
    with session_scope(write=True) as s:
        r = s.scalars(select(Recap).join(Source)
                      .where(Source.discord_id == source.discord_id, Recap.recap_date == d)).one()
        r.updated_at = utc
        return r.id


@pytest.fixture
def seeded(db):
    """
    A (tim Produk): 12, 13, 14 Nov. Tgl 12 punya 2 revisi.
    B (tanpa tim) : 13 Nov, 0 entri.
    """
    save_recap_day(SRC_A, date(2026, 11, 12), [e(U1, "ANDI", opening=True)], "scheduler", TODAY)
    save_recap_day(SRC_A, date(2026, 11, 12), [e(U1, "ANDI", opening=True, closing=True),
                                               e(U2, "SITI", opening_late=True)], "panel:admin", TODAY)
    save_recap_day(SRC_A, date(2026, 11, 12), [e(U2, "SITI", opening_late=True, closing=True),
                                               e(U3, "BUDI", closing_late=True)], "panel:admin", TODAY)
    save_recap_day(SRC_A, date(2026, 11, 13), [e(U2, "SITI", closing=True)], "scheduler", TODAY)
    save_recap_day(SRC_A, date(2026, 11, 14), [e(U1, "ANDI", opening=True)], "scheduler", TODAY)
    save_recap_day(SRC_B, date(2026, 11, 13), [], "scheduler", TODAY)
    set_team(SRC_A.discord_id, "Produk")
    with session_scope() as s:
        ids = {x.discord_id: x.id for x in s.scalars(select(Source))}
    return {"A": ids[SRC_A.discord_id], "B": ids[SRC_B.discord_id]}


def q(client, path, **params):
    return client.get(path, query_string=params)


# ── akses tanpa login ─────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/sources", "/api/recaps", "/api/recaps/1", "/api/recaps/export",
    "/api/recaps/export?date_from=2026-11-12",
])
def test_history_routes_require_login(client, path):
    res = client.get(path)
    assert res.status_code == 401
    assert res.get_json() == {"status": "error", "message": "Belum login."}


# ── WIB ───────────────────────────────────────────────────

@pytest.mark.parametrize("utc, expected", [
    (datetime(2026, 11, 12, 3, 0, 0),   "2026-11-12 10:00:00 WIB"),
    (datetime(2026, 11, 12, 16, 59, 59), "2026-11-12 23:59:59 WIB"),
    (datetime(2026, 11, 12, 17, 0, 0),  "2026-11-13 00:00:00 WIB"),   # lewat tengah malam WIB
    (None, None),
])
def test_to_wib(utc, expected):
    assert to_wib(utc) == expected


# ── filter ────────────────────────────────────────────────

def test_filter_defaults_last_30_days():
    f = parse_filter({}, today=TODAY)
    assert (f.date_from, f.date_to) == (date(2026, 10, 17), TODAY)


def test_filter_single_date_from_either_side():
    assert parse_filter({"date_from": "2026-11-12"}, TODAY).date_to == date(2026, 11, 12)
    assert parse_filter({"date_to": "2026-11-12"}, TODAY).date_from == date(2026, 11, 12)


@pytest.mark.parametrize("args, msg", [
    ({"date_from": "12-11-2026"}, "Format date_from"),
    ({"date_from": "2026-11-13", "date_to": "2026-11-12"}, "tidak boleh lebih besar"),
    ({"date_from": "2025-11-13", "date_to": "2026-11-14"}, f"maksimal {MAX_RANGE_DAYS} hari"),   # 367 hari
    ({"source_id": "abc"}, "source_id harus angka"),
])
def test_filter_invalid(args, msg):
    with pytest.raises(FilterError, match=msg):
        parse_filter(args, TODAY)


def test_filter_max_range_inclusive_ok():
    f = parse_filter({"date_from": "2025-11-15", "date_to": "2026-11-15"}, TODAY)   # tepat 366 hari
    assert (f.date_to - f.date_from).days + 1 == MAX_RANGE_DAYS


# ── daftar riwayat ────────────────────────────────────────

def test_list_all_with_metadata_in_wib(auth_client, seeded):
    set_updated_at(SRC_A, date(2026, 11, 12), datetime(2026, 11, 12, 17, 30, 0))
    body = q(auth_client, "/api/recaps", date_from="2026-11-01", date_to="2026-11-15").get_json()

    assert body["total"] == 4 and body["pages"] == 1
    rows = [(r["recap_date"], r["source"]["name"], r["entry_count"], r["revision_count"]) for r in body["items"]]
    assert rows == [   # urut tanggal terbaru dulu, lalu nama source
        ("2026-11-14", "opening-closing", 1, 0),
        ("2026-11-13", "opening-closing", 1, 0),
        ("2026-11-13", "thread dev", 0, 0),
        ("2026-11-12", "opening-closing", 2, 2),
    ]
    r12 = body["items"][3]
    assert r12["last_triggered_by"] == "panel:admin"
    assert r12["updated_at"] == "2026-11-13 00:30:00 WIB"
    assert r12["source"]["team"] == "Produk" and r12["source"]["discord_id"] == str(SRC_A.discord_id)


def test_list_filter_single_date(auth_client, seeded):
    body = q(auth_client, "/api/recaps", date_from="2026-11-13", date_to="2026-11-13").get_json()
    assert sorted(r["source"]["name"] for r in body["items"]) == ["opening-closing", "thread dev"]


def test_list_filter_source(auth_client, seeded):
    body = q(auth_client, "/api/recaps", date_from="2026-11-01", date_to="2026-11-15",
             source_id=seeded["B"]).get_json()
    assert [(r["recap_date"], r["source"]["kind"]) for r in body["items"]] == [("2026-11-13", "thread")]


@pytest.mark.parametrize("team, expected", [("Produk", 3), ("__none__", 1), ("Development", 0)])
def test_list_filter_team(auth_client, seeded, team, expected):
    body = q(auth_client, "/api/recaps", date_from="2026-11-01", date_to="2026-11-15", team=team).get_json()
    assert body["total"] == expected


def test_list_pagination(auth_client, seeded):
    p1 = q(auth_client, "/api/recaps", date_from="2026-11-01", date_to="2026-11-15", per_page=3).get_json()
    p2 = q(auth_client, "/api/recaps", date_from="2026-11-01", date_to="2026-11-15", per_page=3, page=2).get_json()
    p9 = q(auth_client, "/api/recaps", date_from="2026-11-01", date_to="2026-11-15", per_page=3, page=9).get_json()
    assert (p1["total"], p1["pages"], len(p1["items"]), len(p2["items"]), p9["items"]) == (4, 2, 3, 1, [])
    assert {r["id"] for r in p1["items"]}.isdisjoint(r["id"] for r in p2["items"])


def test_list_per_page_capped(auth_client, seeded):
    assert q(auth_client, "/api/recaps", per_page=10_000).get_json()["per_page"] == 100


@pytest.mark.parametrize("params", [
    {"date_from": "2025-01-01", "date_to": "2026-11-15"},
    {"date_from": "bukan-tanggal"},
    {"page": "0"},
    {"per_page": "x"},
])
def test_list_bad_params_400(auth_client, seeded, params):
    res = q(auth_client, "/api/recaps", **params)
    assert res.status_code == 400 and res.get_json()["status"] == "error"


def test_sources_and_teams(auth_client, seeded):
    body = auth_client.get("/api/sources").get_json()
    assert [(s["name"], s["kind"], s["team"]) for s in body["sources"]] == [
        ("opening-closing", "channel", "Produk"), ("thread dev", "thread", None)]
    assert body["teams"] == ["Produk"]


# ── detail + revisi ───────────────────────────────────────

def test_detail_with_revisions_old_vs_current(auth_client, seeded):
    with session_scope() as s:
        rid = s.scalar(select(Recap.id).where(Recap.source_id == seeded["A"], Recap.recap_date == date(2026, 11, 12)))
    d = auth_client.get(f"/api/recaps/{rid}").get_json()

    assert [x["display_name"] for x in d["entries"]] == ["BUDI", "SITI"]
    assert d["revision_count"] == 2 and d["updated_at"].endswith(" WIB")
    assert [v["revision_no"] for v in d["revisions"]] == [2, 1]          # terbaru dulu

    rev1 = d["revisions"][1]
    assert rev1["replaced_by"] == "panel:admin"
    assert rev1["old_triggered_by"] == "scheduler"
    assert rev1["old_updated_at"].endswith(" WIB") and rev1["replaced_at"].endswith(" WIB")
    assert [x["display_name"] for x in rev1["old_entries"]] == ["ANDI"]
    assert {r["display_name"]: r["status"] for r in rev1["diff_vs_current"]} == {
        "ANDI": "removed", "BUDI": "added", "SITI": "added"}

    rev2 = d["revisions"][0]
    diff2 = {r["display_name"]: r for r in rev2["diff_vs_current"]}
    assert {k: v["status"] for k, v in diff2.items()} == {"ANDI": "removed", "BUDI": "added", "SITI": "changed"}
    assert diff2["SITI"]["old"] == {"opening": False, "opening_late": True, "closing": False, "closing_late": False}
    assert diff2["SITI"]["new"] == {"opening": False, "opening_late": True, "closing": True, "closing_late": False}
    assert diff2["ANDI"]["new"] is None and diff2["BUDI"]["old"] is None
    assert all(isinstance(r["discord_user_id"], str) for r in rev2["diff_vs_current"])


def test_detail_without_revisions(auth_client, seeded):
    with session_scope() as s:
        rid = s.scalar(select(Recap.id).where(Recap.source_id == seeded["B"]))
    d = auth_client.get(f"/api/recaps/{rid}").get_json()
    assert (d["entries"], d["revisions"], d["entry_count"]) == ([], [], 0)


def test_detail_not_found(auth_client, seeded):
    res = auth_client.get("/api/recaps/99999")
    assert res.status_code == 404 and res.get_json()["status"] == "error"


# ── export ────────────────────────────────────────────────

def _load(content: bytes):
    return openpyxl.load_workbook(BytesIO(content))


def test_export_download(auth_client, seeded):
    set_updated_at(SRC_A, date(2026, 11, 12), datetime(2026, 11, 12, 17, 30, 0))
    res = q(auth_client, "/api/recaps/export", date_from="2026-11-12", date_to="2026-11-13")
    assert res.status_code == 200
    assert res.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "recap_2026-11-12_to_2026-11-13_db.xlsx" in res.headers["Content-Disposition"]

    wb = _load(res.data)
    rows = [list(r) for r in wb["Recap"].iter_rows(values_only=True)]
    assert rows == [
        XLSX_HEADER,
        ["2026-11-12", "opening-closing", "BUDI", 0, 0, 0, 1],
        ["2026-11-12", "opening-closing", "SITI", 0, 1, 1, 0],
        ["2026-11-13", "opening-closing", "SITI", 0, 0, 1, 0],
    ]
    info = [list(r) for r in wb["Info"].iter_rows(values_only=True)]
    assert info[0][0] == "exported_at" and info[0][1].endswith(" WIB")
    header_at = info.index(["date", "source", "team", "entries", "revision_count",
                            "last_triggered_by", "updated_at (WIB)"])
    meta = {(r[0], r[1]): r[2:] for r in info[header_at + 1:]}
    assert meta[("2026-11-12", "opening-closing")] == ["Produk", 2, 2, "panel:admin", "2026-11-13 00:30:00 WIB"]
    thread = meta[("2026-11-13", "thread dev")]
    assert thread[:4] == [None, 0, 0, "scheduler"] and thread[4].endswith(" WIB")


def test_export_filters_and_empty(auth_client, seeded):
    wb = _load(q(auth_client, "/api/recaps/export", date_from="2026-11-01", date_to="2026-11-15",
                 team="__none__").data)
    assert [list(r) for r in wb["Recap"].iter_rows(values_only=True)] == [XLSX_HEADER]   # B: 0 entri
    wb = _load(q(auth_client, "/api/recaps/export", date_from="2026-11-14", source_id=seeded["A"]).data)
    assert [list(r) for r in wb["Recap"].iter_rows(values_only=True)][1:] == [
        ["2026-11-14", "opening-closing", "ANDI", 1, 0, 0, 0]]


def test_export_range_limit(auth_client, seeded):
    res = q(auth_client, "/api/recaps/export", date_from="2025-01-01", date_to="2026-11-15")
    assert res.status_code == 400 and "maksimal" in res.get_json()["message"]


def test_export_columns_and_rows_match_discord_excel(db, tmp_path, monkeypatch):
    """Untuk data yang sama, sheet Recap = isi file Excel yang dikirim ke Discord."""
    monkeypatch.chdir(tmp_path)
    daily = {
        date(2026, 11, 12): {
            U2: {"name": "SITI", "op": False, "cl": True, "op_late": True, "cl_late": False},
            U1: {"name": "ANDI", "op": True, "cl": False, "op_late": False, "cl_late": False},
            U3: {"name": "ANDI", "op": False, "cl": False, "op_late": False, "cl_late": True},   # nama sama
        },
        date(2026, 11, 14): {U1: {"name": "ANDI", "op": True, "cl": True, "op_late": False, "cl_late": False}},
    }
    from services.recap_store import save_recap_range
    save_recap_range(SRC_A, date(2026, 11, 12), date(2026, 11, 14), daily, "scheduler", TODAY)

    discord_file = recap._write_xlsx(SRC_A.name, daily, date(2026, 11, 12), date(2026, 11, 14))
    discord_rows = [list(r) for r in openpyxl.load_workbook(discord_file).active.iter_rows(values_only=True)]

    content, _ = export_xlsx(RecapFilter(date(2026, 11, 12), date(2026, 11, 14)))
    db_rows = [list(r) for r in _load(content)["Recap"].iter_rows(values_only=True)]

    assert db_rows[0] == discord_rows[0] == XLSX_HEADER
    assert db_rows == discord_rows
