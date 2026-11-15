"""Langkah 6: login admin, proteksi route, CSRF, rate limit, logout. Data fiktif."""
import re

import pytest
from sqlalchemy import select

from db.base import session_scope
from db.models import AdminUser
from tests.conftest import ADMIN_PASS, ADMIN_USER, do_login, get_csrf
from web.auth import authenticate, create_admin


# ── akses tanpa login ─────────────────────────────────────

@pytest.mark.parametrize("path", ["/", "/index.html", "/tidak-ada"])
def test_pages_redirect_to_login(client, path):
    res = client.get(path)
    assert res.status_code == 302
    assert res.headers["Location"].startswith("/login")


def test_redirect_keeps_next(client):
    res = client.get("/riwayat?tanggal=2026-11-12")
    assert res.headers["Location"] == "/login?next=/riwayat?tanggal%3D2026-11-12"


@pytest.mark.parametrize("method, path", [
    ("GET",  "/static/script.js"),
    ("GET",  "/static/index.html"),
    ("GET",  "/api/me"),
    ("POST", "/recap"),            # tanpa token CSRF pun → 401, bukan 400
    ("POST", "/logout"),
])
def test_api_and_assets_return_401_json(client, method, path):
    res = client.open(path, method=method, json={} if method == "POST" else None)
    assert res.status_code == 401
    assert res.is_json and res.get_json() == {"status": "error", "message": "Belum login."}


@pytest.mark.parametrize("path", ["/login", "/static/style.css", "/static/logo.jpg", "/healthz", "/api/csrf-token"])
def test_public_routes_without_login(client, path):
    assert client.get(path).status_code == 200


def test_static_path_traversal_blocked(auth_client):
    assert auth_client.get("/static/../server.py").status_code == 404


# ── login ─────────────────────────────────────────────────

def test_login_success_sets_session_and_last_login(client, admin):
    res = do_login(client)
    assert res.status_code == 302 and res.headers["Location"] == "/"

    assert client.get("/").status_code == 200
    assert client.get("/static/script.js").status_code == 200
    assert client.get("/api/me").get_json()["username"] == ADMIN_USER

    with session_scope() as s:
        assert s.scalars(select(AdminUser)).one().last_login_at is not None


def test_session_cookie_flags(client, admin):
    res = do_login(client)
    cookie = next(v for k, v in res.headers.items() if k == "Set-Cookie" and v.startswith("session="))
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Secure" not in cookie          # SESSION_COOKIE_SECURE default false (localhost)


@pytest.mark.parametrize("username, password", [
    (ADMIN_USER, "password-salah"),
    ("tidak-ada", ADMIN_PASS),
    (ADMIN_USER, ""),
])
def test_login_wrong_credentials(client, admin, username, password):
    res = do_login(client, username, password)
    assert res.status_code == 401
    assert "Username atau password salah." in res.get_data(as_text=True)
    assert client.get("/api/me").status_code == 401


def test_login_inactive_user_rejected(client, admin):
    with session_scope(write=True) as s:
        s.scalars(select(AdminUser)).one().is_active = False
    assert do_login(client).status_code == 401


def test_deactivated_user_loses_existing_session(auth_client):
    with session_scope(write=True) as s:
        s.scalars(select(AdminUser)).one().is_active = False
    assert auth_client.get("/api/me").status_code == 401


@pytest.mark.parametrize("next_, expected", [
    ("/static/script.js", "/static/script.js"),
    ("https://evil.example.com/", "/"),
    ("//evil.example.com", "/"),
    ("/\\evil.example.com", "/"),
])
def test_login_next_redirect_is_local_only(client, admin, next_, expected):
    res = do_login(client, next_=next_)
    assert res.headers["Location"] == expected


def test_login_page_redirects_when_already_logged_in(auth_client):
    assert auth_client.get("/login").headers["Location"] == "/"


def test_password_is_hashed_not_plaintext(admin):
    with session_scope() as s:
        h = s.scalars(select(AdminUser)).one().password_hash
    assert ADMIN_PASS not in h and h.startswith(("scrypt:", "pbkdf2:"))


def test_login_page_escapes_username(client, admin):
    res = do_login(client, "<script>alert(1)</script>", "x")
    html = res.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


# ── rate limit ────────────────────────────────────────────

def test_rate_limit_after_5_failed_attempts(client, admin):
    for _ in range(5):
        assert do_login(client, password="salah").status_code == 401
    res = do_login(client)                          # password benar pun ditolak sementara
    assert res.status_code == 429
    assert "Terlalu banyak percobaan login" in res.get_data(as_text=True)


def test_successful_logins_not_counted(client, admin):
    for _ in range(6):
        assert do_login(client).status_code == 302
        client.post("/logout", headers={"X-CSRFToken": client.get("/api/me").get_json()["csrf_token"]})
    assert do_login(client, password="salah").status_code == 401   # belum kena limit


# ── CSRF ──────────────────────────────────────────────────

def test_login_without_csrf_rejected(client, admin):
    res = do_login(client, with_csrf=False)
    assert res.status_code == 400
    assert "Sesi form kedaluwarsa" in res.get_data(as_text=True)
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("headers", [{}, {"X-CSRFToken": "token-palsu"}])
def test_recap_without_valid_csrf_rejected(auth_client, headers):
    res = auth_client.post("/recap", json={}, headers=headers)
    assert res.status_code == 400
    assert res.get_json() == {"status": "error", "message": "CSRF token tidak valid atau tidak ada."}


def test_logout_without_csrf_rejected(auth_client):
    assert auth_client.post("/logout").status_code == 400
    assert auth_client.get("/api/me").status_code == 200       # masih login


def test_csrf_token_in_login_form(client):
    html = client.get("/login").get_data(as_text=True)
    assert re.search(r'name="csrf_token" value="[^"]+"', html)


# ── logout ────────────────────────────────────────────────

def test_logout(auth_client):
    res = auth_client.post("/logout", headers={"X-CSRFToken": auth_client.csrf})
    assert res.status_code == 302 and res.headers["Location"] == "/login"
    assert auth_client.get("/").status_code == 302
    assert auth_client.get("/api/me").status_code == 401


def test_logout_requires_post(auth_client):
    assert auth_client.get("/logout").status_code == 405
    assert auth_client.get("/api/me").status_code == 200


# ── create-admin ──────────────────────────────────────────

@pytest.mark.parametrize("username, password, msg", [
    ("ab", "panjang-sekali", "Username"),
    ("nama spasi", "panjang-sekali", "Username"),
    ("valid_user", "pendek", "minimal 8"),
])
def test_create_admin_validation(db, username, password, msg):
    with pytest.raises(ValueError, match=msg):
        create_admin(username, password)


def test_create_admin_duplicate_rejected(admin):
    with pytest.raises(ValueError, match="sudah ada"):
        create_admin(ADMIN_USER, "password-lain-123")


def test_create_admin_cli(app):
    runner = app.test_cli_runner()
    res = runner.invoke(args=["create-admin", "--username", "operator"],
                        input="password-operator\npassword-operator\n")
    assert res.exit_code == 0, res.output
    assert "berhasil dibuat" in res.output
    assert "password-operator" not in res.output         # password tidak ditampilkan
    assert authenticate("operator", "password-operator") is not None

    res = runner.invoke(args=["create-admin", "--username", "operator"],
                        input="password-operator\npassword-operator\n")
    assert res.exit_code != 0 and "sudah ada" in res.output


def test_create_admin_cli_password_mismatch(app):
    res = app.test_cli_runner().invoke(args=["create-admin", "--username", "operator"],
                                       input="password-satu\npassword-dua\npassword-satu\npassword-satu\n")
    assert res.exit_code == 0
    assert authenticate("operator", "password-satu") is not None


# ── session fixation & lifetime ───────────────────────────

def test_login_clears_previous_session(client, admin):
    with client.session_transaction() as sess:
        sess["ditanam_penyerang"] = "x"
        sess["_user_id"] = "999"               # mencoba "menanam" identitas lain
    old_csrf = get_csrf(client)

    res = client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASS, "csrf_token": old_csrf})
    assert res.status_code == 302

    with client.session_transaction() as sess:
        assert "ditanam_penyerang" not in sess
        assert sess["_user_id"] != "999"
        assert sess.get("csrf_token") is None   # token lama ikut dibuang
    me = client.get("/api/me").get_json()
    assert me["username"] == ADMIN_USER and me["csrf_token"] != old_csrf


def test_failed_login_does_not_clear_session(client, admin):
    with client.session_transaction() as sess:
        sess["x"] = "tetap"
    do_login(client, password="salah")
    with client.session_transaction() as sess:
        assert sess["x"] == "tetap"


def _session_cookie(res):
    return next(v for k, v in res.headers.items() if k == "Set-Cookie" and v.startswith("session="))


def test_session_lifetime_default_browser_session(client, admin):
    cookie = _session_cookie(do_login(client))
    assert "Expires=" not in cookie and "Max-Age" not in cookie


def test_session_lifetime_when_configured(app, client, admin, monkeypatch):
    from datetime import timedelta
    monkeypatch.setitem(app.config, "RADSOFT_PERMANENT_SESSION", True)
    monkeypatch.setitem(app.config, "PERMANENT_SESSION_LIFETIME", timedelta(minutes=30))
    cookie = _session_cookie(do_login(client))
    assert "Expires=" in cookie
    with client.session_transaction() as sess:
        assert sess.permanent is True


@pytest.mark.parametrize("raw, expected", [("", None), ("  ", None), ("30", 30)])
def test_env_session_lifetime_parsing(monkeypatch, raw, expected):
    import config
    monkeypatch.setenv("SESSION_LIFETIME_MINUTES", raw)
    assert config._env_positive_int("SESSION_LIFETIME_MINUTES") == expected


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "1.5"])
def test_env_session_lifetime_invalid(monkeypatch, raw):
    import config
    monkeypatch.setenv("SESSION_LIFETIME_MINUTES", raw)
    with pytest.raises(SystemExit, match="SESSION_LIFETIME_MINUTES"):
        config._env_positive_int("SESSION_LIFETIME_MINUTES")
