import threading
from datetime import date

import pytest
from sqlalchemy import select, func

import services.recap_store as store
from db.base import session_scope
from db.models import Source, Recap, RecapEntry, RecapRevision
from services.recap_store import (
    SourceInfo, EntryIn, save_recap_day, save_recap_range, entries_from_day_data,
    CREATED, UPDATED, UNCHANGED, SKIPPED, ERROR,
)

SRC   = SourceInfo(discord_id=1493500756000000001, kind="channel", name="opening-closing")
D     = date(2026, 11, 12)
TODAY = date(2026, 11, 14)
UID_A = 299502622593777665     # > 2^53, ID Discord realistis
UID_B = 1116155926009880597


def entry(uid, name, **flags):
    return EntryIn(discord_user_id=uid, display_name=name, **flags)


def load(recap_date=D, source=SRC):
    with session_scope() as s:
        r = s.scalars(select(Recap).join(Source)
                      .where(Source.discord_id == source.discord_id, Recap.recap_date == recap_date)).one()
        return {
            "revision_count": r.revision_count,
            "last_triggered_by": r.last_triggered_by,
            "updated_at": r.updated_at,
            "entries": {e.discord_user_id: (e.display_name, e.opening, e.opening_late, e.closing, e.closing_late)
                        for e in r.entries},
            "revisions": [(v.revision_no, v.triggered_by, v.snapshot) for v in r.revisions],
        }


def count(model):
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model))


# ── simpan pertama ────────────────────────────────────────

def test_first_save_creates_recap(db):
    res = save_recap_day(SRC, D, [entry(UID_A, "FIKRI", opening=True, closing=True),
                                  entry(UID_B, "FAHRIZAL", opening_late=True)], "scheduler", TODAY)
    assert res.status == CREATED and res.recap_id

    got = load()
    assert got["revision_count"] == 0
    assert got["last_triggered_by"] == "scheduler"
    assert got["entries"] == {
        UID_A: ("FIKRI", True, False, True, False),
        UID_B: ("FAHRIZAL", False, True, False, False),
    }
    assert got["revisions"] == []
    with session_scope() as s:
        src = s.scalars(select(Source)).one()
        assert (src.discord_id, src.kind, src.name, src.team) == (SRC.discord_id, "channel", SRC.name, None)


# ── rewrite tanggal yang sama ─────────────────────────────

def test_rewrite_same_date_replaces_and_logs_revision(db):
    save_recap_day(SRC, D, [entry(UID_A, "FIKRI", opening=True)], "scheduler", TODAY)
    res = save_recap_day(SRC, D, [entry(UID_A, "FIKRI", opening=True, closing=True),
                                  entry(UID_B, "FAHRIZAL", closing_late=True)], "panel:admin", TODAY)
    assert res.status == UPDATED and res.revision_no == 1

    got = load()
    assert count(Recap) == 1                       # tidak duplikat
    assert got["revision_count"] == 1
    assert got["last_triggered_by"] == "panel:admin"
    assert got["entries"] == {
        UID_A: ("FIKRI", True, False, True, False),
        UID_B: ("FAHRIZAL", False, False, False, True),
    }

    [(no, by, snap)] = got["revisions"]
    assert (no, by) == (1, "panel:admin")
    assert snap["triggered_by"] == "scheduler"     # versi lama
    assert snap["revision_count"] == 0
    assert snap["entries"] == [{"discord_user_id": str(UID_A), "display_name": "FIKRI",
                                "opening": True, "opening_late": False,
                                "closing": False, "closing_late": False}]


def test_multiple_rewrites_increment_revision_no(db):
    for i, flags in enumerate([dict(opening=True), dict(closing=True), dict(opening_late=True)]):
        save_recap_day(SRC, D, [entry(UID_A, "FIKRI", **flags)], f"run{i}", TODAY)
    got = load()
    assert got["revision_count"] == 2
    assert [(no, snap["triggered_by"]) for no, _, snap in got["revisions"]] == [(1, "run0"), (2, "run1")]
    assert count(RecapEntry) == 1


# ── rekap identik ─────────────────────────────────────────

def test_identical_rerun_no_revision(db):
    entries = [entry(UID_A, "FIKRI", opening=True), entry(UID_B, "FAHRIZAL", closing=True)]
    save_recap_day(SRC, D, entries, "scheduler", TODAY)
    before = load()

    res = save_recap_day(SRC, D, list(reversed(entries)), "panel:admin", TODAY)   # urutan beda
    assert res.status == UNCHANGED and res.names_updated == 0

    after = load()
    assert after["revision_count"] == 0 and after["revisions"] == []
    assert after["entries"] == before["entries"]
    assert after["last_triggered_by"] == "panel:admin"
    assert after["updated_at"] >= before["updated_at"]


def test_display_name_only_change_updates_name_without_revision(db):
    save_recap_day(SRC, D, [entry(UID_A, "FIKRY", opening=True)], "scheduler", TODAY)
    res = save_recap_day(SRC, D, [entry(UID_A, "FIKRI AVISHENA", opening=True)], "panel:admin", TODAY)

    assert res.status == UNCHANGED and res.names_updated == 1
    got = load()
    assert got["entries"][UID_A][0] == "FIKRI AVISHENA"
    assert got["revision_count"] == 0 and got["revisions"] == []


def test_source_name_change_is_updated(db):
    save_recap_day(SRC, D, [], "scheduler", TODAY)
    renamed = SourceInfo(SRC.discord_id, SRC.kind, "renamed-channel")
    save_recap_day(renamed, D, [], "scheduler", TODAY)
    with session_scope() as s:
        assert [x.name for x in s.scalars(select(Source))] == ["renamed-channel"]


# ── tanggal kosong ────────────────────────────────────────

def test_empty_date_saved_with_zero_entries(db):
    res = save_recap_day(SRC, D, [], "scheduler", TODAY)
    assert res.status == CREATED
    assert load()["entries"] == {}


def test_rerun_empty_clears_previous_data_with_revision(db):
    save_recap_day(SRC, D, [entry(UID_A, "FIKRI", opening=True)], "scheduler", TODAY)
    res = save_recap_day(SRC, D, [], "panel:admin", TODAY)      # pesan sudah dihapus
    assert res.status == UPDATED
    got = load()
    assert got["entries"] == {}
    assert len(got["revisions"][0][2]["entries"]) == 1


def test_empty_rerun_of_empty_is_unchanged(db):
    save_recap_day(SRC, D, [], "scheduler", TODAY)
    assert save_recap_day(SRC, D, [], "scheduler", TODAY).status == UNCHANGED


# ── tanggal di masa depan ─────────────────────────────────

def test_future_date_skipped(db):
    res = save_recap_day(SRC, date(2026, 11, 15), [entry(UID_A, "FIKRI", opening=True)], "panel:admin", TODAY)
    assert res.status == SKIPPED
    assert count(Recap) == 0 and count(Source) == 0


def test_today_is_saved(db):
    assert save_recap_day(SRC, TODAY, [], "panel:admin", TODAY).status == CREATED


# ── rentang tanggal ───────────────────────────────────────

def _daily(**by_day):
    """helper: _daily(d12={...}) → {date(2026,11,12): {...}}"""
    return {date(2026, 11, int(k[1:])): v for k, v in by_day.items()}


def test_range_saves_each_date_including_empty_and_future(db):
    daily = _daily(
        d12={"FIKRI": {"uid": UID_A, "op": True, "cl": True, "op_late": False, "cl_late": False}},
        d14={"FAHRIZAL": {"uid": UID_B, "op": False, "cl": False, "op_late": True, "cl_late": False}},
    )
    res = save_recap_range(SRC, date(2026, 11, 12), date(2026, 11, 15), daily, "panel:admin", TODAY)

    assert [(r.recap_date.day, r.status) for r in res] == [
        (12, CREATED), (13, CREATED), (14, CREATED), (15, SKIPPED)]
    assert load(date(2026, 11, 12))["entries"] == {UID_A: ("FIKRI", True, False, True, False)}
    assert load(date(2026, 11, 13))["entries"] == {}
    assert load(date(2026, 11, 14))["entries"] == {UID_B: ("FAHRIZAL", False, True, False, False)}


def test_range_rerun_mixed_statuses(db):
    d12 = {"FIKRI": {"uid": UID_A, "op": True, "cl": False, "op_late": False, "cl_late": False}}
    save_recap_range(SRC, date(2026, 11, 12), date(2026, 11, 13), _daily(d12=d12), "scheduler", TODAY)

    d13 = {"FIKRI": {"uid": UID_A, "op": False, "cl": True, "op_late": False, "cl_late": False}}
    res = save_recap_range(SRC, date(2026, 11, 12), date(2026, 11, 13), _daily(d12=d12, d13=d13), "panel:admin", TODAY)
    assert [r.status for r in res] == [UNCHANGED, UPDATED]


def test_range_error_on_one_date_does_not_cancel_others(db, monkeypatch, caplog):
    real = store.save_recap_day

    def flaky(source, d, *a, **kw):
        if d == date(2026, 11, 13):
            raise RuntimeError("disk penuh")
        return real(source, d, *a, **kw)

    monkeypatch.setattr(store, "save_recap_day", flaky)
    res = save_recap_range(SRC, date(2026, 11, 12), date(2026, 11, 14), {}, "scheduler", TODAY)

    assert [r.status for r in res] == [CREATED, ERROR, CREATED]
    assert res[1].error == "RuntimeError: disk penuh"
    assert count(Recap) == 2
    assert "Gagal menyimpan rekap" in caplog.text


def test_entries_from_day_data_requires_uid():
    with pytest.raises(ValueError):
        entries_from_day_data({"FIKRI": {"op": True}})


def test_duplicate_uid_rejected(db):
    with pytest.raises(ValueError):
        save_recap_day(SRC, D, [entry(UID_A, "A"), entry(UID_A, "B")], "scheduler", TODAY)
    assert count(Recap) == 0


# ── dua rekap bersamaan ───────────────────────────────────

def test_concurrent_saves_same_date_are_serialized(db):
    barrier = threading.Barrier(2)
    results, errors = [], []

    def run(flags, who):
        try:
            barrier.wait()
            results.append(save_recap_day(SRC, D, [entry(UID_A, "FIKRI", **flags)], who, TODAY))
        except Exception as e:      # pragma: no cover - harus tidak terjadi
            errors.append(e)

    threads = [threading.Thread(target=run, args=(dict(opening=True), "scheduler")),
               threading.Thread(target=run, args=(dict(closing=True), "panel:admin"))]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    assert sorted(r.status for r in results) == [CREATED, UPDATED]
    assert count(Recap) == 1 and count(Source) == 1 and count(RecapEntry) == 1
    assert load()["revision_count"] == 1
