"""
CLI pemetaan source → tim:

    flask --app web.server list-sources
    flask --app web.server set-team <discord_id> <Produk|Implementor|Development>
    flask --app web.server clear-team <discord_id>
"""
import click
from flask import Flask

from services.source_admin import TEAMS, SourceAdminError, clear_team, list_sources, set_team


def _team_label(team: str | None) -> str:
    return team or "-"


@click.command("list-sources")
def list_sources_command():
    """Tampilkan semua source (config + database) beserta timnya."""
    rows = list_sources()
    if not rows:
        click.echo("Belum ada source. Isi channel_ids / thread_ids di appsettings.json.")
        return

    header = ("DISCORD_ID", "KIND", "NAME", "TEAM", "STATUS")
    table = [header] + [
        (str(r.discord_id), r.kind, r.name, _team_label(r.team),
         "ok" if r.in_config and r.in_db else
         "belum direkap" if r.in_config else
         "tidak ada di config")
        for r in rows
    ]
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    for row in table:
        click.echo("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
    click.echo(f"\nTim yang tersedia: {', '.join(TEAMS)}")


@click.command("set-team")
@click.argument("discord_id", type=int)
@click.argument("team")
def set_team_command(discord_id: int, team: str):
    """Petakan source DISCORD_ID ke TEAM (Produk / Implementor / Development)."""
    try:
        ch = set_team(discord_id, team)
    except SourceAdminError as e:
        raise click.ClickException(str(e))
    note = " (source belum pernah direkap; nama diisi otomatis saat rekap berikutnya)" if ch.created else ""
    click.echo(f"{ch.discord_id} {ch.name}: {_team_label(ch.old_team)} → {ch.new_team}{note}")


@click.command("clear-team")
@click.argument("discord_id", type=int)
def clear_team_command(discord_id: int):
    """Hapus pemetaan tim dari source DISCORD_ID."""
    try:
        ch = clear_team(discord_id)
    except SourceAdminError as e:
        raise click.ClickException(str(e))
    if ch.old_team is None:
        click.echo(f"{ch.discord_id} {ch.name}: memang belum punya tim.")
    else:
        click.echo(f"{ch.discord_id} {ch.name}: {ch.old_team} → -")


def register_cli(app: Flask) -> None:
    app.cli.add_command(list_sources_command)
    app.cli.add_command(set_team_command)
    app.cli.add_command(clear_team_command)
