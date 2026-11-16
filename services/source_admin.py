"""
Pemetaan source (channel / thread Discord) → tim.

- Tim dibatasi ke TEAMS (case-insensitive, disimpan dengan kapitalisasi baku).
- set-team hanya untuk ID yang ada di channel_ids / thread_ids (appsettings.json).
  Source yang belum pernah direkap tetap bisa dipetakan: baris `sources`
  dibuat dengan kind dari config dan nama sementara PLACEHOLDER_NAME, lalu
  nama aslinya diisi otomatis saat rekap berikutnya (upsert di recap_store).
- clear-team boleh untuk source apa pun yang sudah ada di database.
"""
from dataclasses import dataclass

from sqlalchemy import select

import config
from db.base import session_scope
from db.models import Source

TEAMS = ("Produk", "Implementor", "Development")
PLACEHOLDER_NAME = "(belum direkap)"


class SourceAdminError(ValueError):
    """Input tidak valid → pesan untuk ditampilkan ke admin."""


def normalize_team(raw: str) -> str:
    """'produk' / ' PRODUK ' → 'Produk'. Nilai lain → SourceAdminError."""
    value = (raw or "").strip().casefold()
    for team in TEAMS:
        if team.casefold() == value:
            return team
    raise SourceAdminError(
        f"Tim tidak dikenal: {raw!r}. Pilihan: {', '.join(TEAMS)} (huruf besar/kecil bebas)."
    )


def configured_sources() -> dict[int, str]:
    """{discord_id: kind} dari appsettings.json. Kalau ID ada di keduanya, channel menang."""
    out = {int(tid): "thread" for tid in config.THREAD_IDS}
    out.update({int(cid): "channel" for cid in config.CHANNEL_IDS})
    return out


@dataclass(frozen=True)
class SourceRow:
    discord_id: int
    kind: str
    name: str
    team: str | None
    in_config: bool
    in_db: bool


@dataclass(frozen=True)
class TeamChange:
    discord_id: int
    name: str
    old_team: str | None
    new_team: str | None
    created: bool          # baris sources baru dibuat (source belum pernah direkap)


def list_sources() -> list[SourceRow]:
    """Gabungan source di config dan di database, urut: di config dulu, lalu nama."""
    cfg = configured_sources()
    with session_scope() as s:
        db_rows = {x.discord_id: x for x in s.scalars(select(Source))}
        rows = []
        for did in cfg.keys() | db_rows.keys():
            src = db_rows.get(did)
            rows.append(SourceRow(
                discord_id=did,
                kind=src.kind if src else cfg[did],
                name=src.name if src else PLACEHOLDER_NAME,
                team=src.team if src else None,
                in_config=did in cfg,
                in_db=src is not None,
            ))
    return sorted(rows, key=lambda r: (not r.in_config, r.name.casefold(), r.discord_id))


def set_team(discord_id: int, team: str) -> TeamChange:
    canonical = normalize_team(team)
    cfg = configured_sources()
    if discord_id not in cfg:
        raise SourceAdminError(
            f"ID {discord_id} tidak ada di channel_ids / thread_ids (appsettings.json). "
            "Tambahkan dulu ke config, atau cek ID-nya dengan list-sources."
        )

    with session_scope(write=True) as s:
        src = s.scalars(select(Source).where(Source.discord_id == discord_id)).one_or_none()
        created = src is None
        if created:
            src = Source(discord_id=discord_id, kind=cfg[discord_id], name=PLACEHOLDER_NAME)
            s.add(src)
        old = src.team
        src.team = canonical
        return TeamChange(discord_id, src.name, old, canonical, created)


def clear_team(discord_id: int) -> TeamChange:
    with session_scope(write=True) as s:
        src = s.scalars(select(Source).where(Source.discord_id == discord_id)).one_or_none()
        if src is None:
            if discord_id in configured_sources():
                # ada di config tapi belum pernah direkap / dipetakan → memang belum punya tim
                return TeamChange(discord_id, PLACEHOLDER_NAME, None, None, False)
            raise SourceAdminError(f"ID {discord_id} tidak ditemukan di database maupun di config.")
        old = src.team
        src.team = None
        return TeamChange(discord_id, src.name, old, None, False)
