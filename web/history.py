"""
API riwayat rekap (JSON) + export Excel. Semua wajib login lewat guard global
di web/auth.py (blueprint ini tidak perlu decorator apa pun).
"""
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from services.recap_query import (
    DEFAULT_PER_PAGE, FilterError, export_xlsx, get_recap_detail,
    list_recaps, list_sources, parse_filter, parse_int,
)

bp = Blueprint("history", __name__, url_prefix="/api")

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _bad_request(e: Exception):
    return jsonify({"status": "error", "message": str(e)}), 400


@bp.get("/sources")
def sources():
    return jsonify(list_sources())


@bp.get("/recaps")
def recaps():
    """?date_from&date_to&source_id&team&page&per_page"""
    try:
        f        = parse_filter(request.args)
        page     = parse_int("page", request.args.get("page"), default=1)
        per_page = parse_int("per_page", request.args.get("per_page"), default=DEFAULT_PER_PAGE)
    except FilterError as e:
        return _bad_request(e)
    return jsonify(list_recaps(f, page, per_page))


@bp.get("/recaps/<int:recap_id>")
def recap_detail(recap_id: int):
    detail = get_recap_detail(recap_id)
    if detail is None:
        return jsonify({"status": "error", "message": "Rekap tidak ditemukan."}), 404
    return jsonify(detail)


@bp.get("/recaps/export")
def recap_export():
    """?date_from&date_to&source_id&team → file .xlsx (dibuat di memori)."""
    try:
        f = parse_filter(request.args)
    except FilterError as e:
        return _bad_request(e)
    content, filename = export_xlsx(f)
    return send_file(BytesIO(content), mimetype=XLSX_MIME, as_attachment=True, download_name=filename)
