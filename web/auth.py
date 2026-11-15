"""
Login admin panel.

- Flask-Login (session cookie), password di-hash dengan werkzeug (scrypt).
- SEMUA request wajib login lewat guard global `require_login`, kecuali
  PUBLIC_ENDPOINTS dan PUBLIC_ASSETS. Route baru otomatis terlindungi.
- Belum login: halaman (GET non-API) → redirect ke /login;
  API / asset / POST → 401 JSON.
- CSRF: Flask-WTF CSRFProtect untuk semua POST. Form login memakai hidden
  field; fetch dari script.js memakai header X-CSRFToken (token dari /api/me).
- Rate limit: hanya percobaan login GAGAL yang dihitung (Flask-Limiter, memory).
- Admin dibuat lewat CLI: `flask --app web.server create-admin`.
"""
import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

import click
from flask import Blueprint, Flask, current_app, jsonify, redirect, render_template, request, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, UserMixin, current_user, login_user, logout_user
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from config import LOGIN_RATE_LIMIT, SECRET_KEY, SESSION_COOKIE_SECURE, SESSION_LIFETIME_MINUTES
from db.base import session_scope, utcnow
from db.models import AdminUser

bp            = Blueprint("auth", __name__)
login_manager = LoginManager()
csrf          = CSRFProtect()
limiter       = Limiter(get_remote_address, storage_uri="memory://")

PUBLIC_ENDPOINTS = {"auth.login", "auth.csrf_token", "health"}
PUBLIC_ASSETS    = {"style.css", "logo.jpg"}          # dipakai halaman login

USERNAME_RE  = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
MIN_PASSWORD = 8

# Dipakai saat username tidak ada, supaya waktu respons sama (tidak bocor
# apakah username terdaftar).
_DUMMY_HASH = generate_password_hash("radsoft-dummy-password")


# ── user ──────────────────────────────────────────────────

@dataclass
class PanelUser(UserMixin):
    id: int
    username: str
    role: str

    def get_id(self) -> str:
        return str(self.id)


@login_manager.user_loader
def load_user(user_id: str) -> PanelUser | None:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    with session_scope() as s:
        u = s.get(AdminUser, uid)
        if u is None or not u.is_active:
            return None
        return PanelUser(u.id, u.username, u.role)


def create_admin(username: str, password: str, role: str = "admin") -> int:
    """Buat akun admin. Raise ValueError kalau input tidak valid / sudah ada."""
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("Username 3–64 karakter: huruf, angka, titik, garis bawah, atau strip.")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"Password minimal {MIN_PASSWORD} karakter.")

    with session_scope(write=True) as s:
        if s.scalars(select(AdminUser).where(AdminUser.username == username)).first():
            raise ValueError(f"Username '{username}' sudah ada.")
        user = AdminUser(username=username, password_hash=generate_password_hash(password), role=role)
        s.add(user)
        s.flush()
        return user.id


def authenticate(username: str, password: str) -> PanelUser | None:
    """Cek username + password. Sukses → catat last_login_at."""
    username = (username or "").strip()
    with session_scope(write=True) as s:
        u = s.scalars(select(AdminUser).where(AdminUser.username == username)).one_or_none()
        if u is None:
            check_password_hash(_DUMMY_HASH, password or "")
            return None
        if not check_password_hash(u.password_hash, password or "") or not u.is_active:
            return None
        u.last_login_at = utcnow()
        return PanelUser(u.id, u.username, u.role)


# ── helpers ───────────────────────────────────────────────

def _safe_next(target: str | None) -> str:
    """Hanya path lokal (cegah open redirect ke situs lain)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return "/"
    return target


def _wants_redirect() -> bool:
    """Navigasi halaman (GET non-API) → redirect; selain itu → 401 JSON."""
    return request.method == "GET" and not request.path.startswith(("/api/", "/static/", "/recap"))


def _json_error(message: str, status: int):
    return jsonify({"status": "error", "message": message}), status


# ── guard global ──────────────────────────────────────────

@bp.before_app_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if request.endpoint == "static_files" and (request.view_args or {}).get("filename") in PUBLIC_ASSETS:
        return None
    if current_user.is_authenticated:
        return None
    if _wants_redirect():
        nxt = request.full_path.rstrip("?")
        return redirect(url_for("auth.login", next=nxt if nxt != "/" else None))
    return _json_error("Belum login.", 401)


# ── routes ────────────────────────────────────────────────

@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(LOGIN_RATE_LIMIT, methods=["POST"], deduct_when=lambda resp: resp.status_code != 302)
def login():
    nxt = request.values.get("next", "")

    if request.method == "GET":
        if current_user.is_authenticated:
            return redirect(_safe_next(nxt))
        return render_template("login.html", next=nxt)

    username = request.form.get("username", "")
    user = authenticate(username, request.form.get("password", ""))
    if user is None:
        return render_template("login.html", next=nxt, username=username,
                               error="Username atau password salah."), 401

    # Cegah session fixation: buang semua isi session lama (termasuk token
    # CSRF lama) sebelum menandai user sebagai login.
    session.clear()
    session.permanent = current_app.config.get("RADSOFT_PERMANENT_SESSION", False)
    login_user(user)
    return redirect(_safe_next(nxt))


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/api/csrf-token")
def csrf_token():
    """Publik: token CSRF untuk form login (dipakai bot.http)."""
    return jsonify({"csrf_token": generate_csrf()})


@bp.route("/api/me")
def me():
    return jsonify({"username": current_user.username, "role": current_user.role,
                    "csrf_token": generate_csrf()})


# ── CLI ───────────────────────────────────────────────────

@click.command("create-admin")
@click.option("--username", prompt="Username")
@click.password_option("--password", prompt="Password", confirmation_prompt="Ulangi password")
def create_admin_command(username: str, password: str):
    """Buat akun admin panel (tidak ada halaman register)."""
    try:
        create_admin(username, password)
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(f"Admin '{username.strip()}' berhasil dibuat.")


# ── setup ─────────────────────────────────────────────────

def init_auth(app: Flask) -> None:
    app.config.update(
        SECRET_KEY=SECRET_KEY or None,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        # Token CSRF berlaku selama session (panel bisa dibiarkan terbuka lama).
        WTF_CSRF_TIME_LIMIT=None,
    )
    # SESSION_LIFETIME_MINUTES kosong → session cookie berakhir saat browser ditutup.
    # Diisi → session permanen; habis setelah N menit tanpa request
    # (Flask memperbarui cookie di setiap request).
    if SESSION_LIFETIME_MINUTES:
        app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=SESSION_LIFETIME_MINUTES)
        app.config["RADSOFT_PERMANENT_SESSION"] = True

    # Urutan penting: guard login (blueprint) didaftarkan SEBELUM CSRFProtect,
    # supaya request tanpa login dapat 401, bukan 400 CSRF.
    app.register_blueprint(bp)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    app.cli.add_command(create_admin_command)

    @app.errorhandler(CSRFError)
    def _csrf_error(e):
        if request.endpoint == "auth.login":
            return render_template("login.html", next=request.values.get("next", ""),
                                   error="Sesi form kedaluwarsa. Silakan coba lagi."), 400
        return _json_error("CSRF token tidak valid atau tidak ada.", 400)

    @app.errorhandler(429)
    def _too_many(e):
        if request.endpoint == "auth.login":
            return render_template("login.html", next=request.values.get("next", ""),
                                   error="Terlalu banyak percobaan login. Coba lagi sebentar lagi."), 429
        return _json_error("Terlalu banyak request.", 429)
