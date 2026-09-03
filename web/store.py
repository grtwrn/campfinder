"""SQLite storage and account handling for the camp planner site.

Two tables carry everything that matters:

  recipes  - a user's cookbook. One row per recipe, ingredients as JSON.
             Reusable across every trip they plan.
  trips    - one row per trip, holding the plan.py trip dict verbatim as
             JSON (minus recipes). Storing the exact shape plan.py already
             consumes means the site and the CLI never drift apart: the
             generator is the same code path either way.

Passwords use scrypt from the standard library with a per-user salt, so the
deployment needs no crypto dependency. Sessions are opaque random tokens
stored server-side, not signed cookies, so logging out actually revokes.
"""

import datetime as dt
import hashlib
import json
import os
import secrets
import sqlite3

DB_PATH = os.environ.get("CAMP_DB", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "camp.db"))

SESSION_DAYS = 30
# scrypt cost. n=2**14 with r=8 needs 128*n*r = 16 MB per hash and lands around
# 50-100ms on small hardware - that cost is the brake on offline cracking.
# maxmem must be set explicitly: OpenSSL defaults to a 32 MB cap and raises
# "memory limit exceeded" rather than degrading, which is easy to misread as
# an application error.
SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=64, maxmem=64 * 1024 * 1024)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  email      TEXT UNIQUE NOT NULL COLLATE NOCASE,
  name       TEXT NOT NULL DEFAULT '',
  salt       BLOB NOT NULL,
  pw_hash    BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  ingredients TEXT NOT NULL,
  source      TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS trips (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  data       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recipes_user ON recipes(user_id);
CREATE INDEX IF NOT EXISTS idx_trips_user   ON trips(user_id);
CREATE INDEX IF NOT EXISTS idx_sess_user    ON sessions(user_id);
"""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init():
    with connect() as con:
        con.executescript(SCHEMA)


# ------------------------------------------------------------- accounts --
def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    return salt, hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)


def create_user(email, password, name=""):
    salt, digest = hash_password(password)
    with connect() as con:
        cur = con.execute(
            "INSERT INTO users (email, name, salt, pw_hash, created_at) "
            "VALUES (?,?,?,?,?)",
            (email.strip(), name.strip(), salt, digest, now()))
        return cur.lastrowid


def verify_user(email, password):
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?",
                          (email.strip(),)).fetchone()
    if not row:
        # Hash anyway so a missing account and a wrong password take the same
        # time; otherwise the response time enumerates who has an account.
        hash_password(password)
        return None
    _, digest = hash_password(password, row["salt"])
    if secrets.compare_digest(digest, row["pw_hash"]):
        return dict(row)
    return None


def start_session(user_id):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=SESSION_DAYS)
    with connect() as con:
        con.execute("INSERT INTO sessions (token, user_id, csrf, created_at, "
                    "expires_at) VALUES (?,?,?,?,?)",
                    (token, user_id, csrf, now(), exp.isoformat()))
    return token


def session_user(token):
    if not token:
        return None
    with connect() as con:
        row = con.execute(
            "SELECT s.csrf, s.expires_at, u.* FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,)).fetchone()
    if not row or row["expires_at"] < now():
        return None
    return dict(row)


def end_session(token):
    if token:
        with connect() as con:
            con.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ------------------------------------------------------------- cookbook --
def list_recipes(user_id):
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM recipes WHERE user_id = ? ORDER BY name COLLATE NOCASE",
            (user_id,)).fetchall()
    return [{**dict(r), "ingredients": json.loads(r["ingredients"])}
            for r in rows]


def recipe_map(user_id):
    return {r["name"]: r["ingredients"] for r in list_recipes(user_id)}


def save_recipe(user_id, name, ingredients, source=""):
    """Insert or replace by name - re-importing a cookbook updates in place."""
    name = name.strip()
    if not name:
        return
    with connect() as con:
        con.execute(
            "INSERT INTO recipes (user_id, name, ingredients, source, created_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(user_id, name) DO UPDATE SET "
            "ingredients = excluded.ingredients, source = excluded.source",
            (user_id, name, json.dumps(ingredients), source, now()))


def delete_recipe(user_id, recipe_id):
    with connect() as con:
        con.execute("DELETE FROM recipes WHERE id = ? AND user_id = ?",
                    (recipe_id, user_id))


# ---------------------------------------------------------------- trips --
def list_trips(user_id):
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM trips WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)).fetchall()
    return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def get_trip(user_id, trip_id):
    with connect() as con:
        row = con.execute("SELECT * FROM trips WHERE id = ? AND user_id = ?",
                          (trip_id, user_id)).fetchone()
    if not row:
        return None
    return {**dict(row), "data": json.loads(row["data"])}


def create_trip(user_id, name, data):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO trips (user_id, name, data, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, name, json.dumps(data), now(), now()))
        return cur.lastrowid


def save_trip(user_id, trip_id, data, name=None):
    with connect() as con:
        con.execute(
            "UPDATE trips SET data = ?, updated_at = ?"
            + (", name = ?" if name else "")
            + " WHERE id = ? AND user_id = ?",
            ((json.dumps(data), now(), name, trip_id, user_id) if name
             else (json.dumps(data), now(), trip_id, user_id)))


def delete_trip(user_id, trip_id):
    with connect() as con:
        con.execute("DELETE FROM trips WHERE id = ? AND user_id = ?",
                    (trip_id, user_id))
