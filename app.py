import os
import sqlite3
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, request, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "bloomwell.db"

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "development-only-change-this-secret-before-deployment"
)

# Local frontend development plus the existing GitHub Pages origin.
ALLOWED_ORIGINS = [
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "https://krithika-muya-codes.github.io",
]
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# For HTTPS deployment, set COOKIE_SECURE=1 and FLASK_SECRET_KEY to a
# long random secret in the hosting provider's environment settings.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE") == "1"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    SESSION_COOKIE_SAMESITE="None" if COOKIE_SECURE else "Lax",
)


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def add_missing_columns(conn, table, columns):
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db():
    """Create required tables and safely add fields to the earlier prototype DB."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS typing_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            wpm REAL,
            accuracy REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            activity_name TEXT,
            duration_minutes REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS speech_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            record_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS mood_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mood TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    # Earlier versions of database.py created activity_logs with only
    # activity_name and duration_minutes. Add fields without deleting data.
    add_missing_columns(conn, "activity_logs", {
        "activity_date": "TEXT",
        "screen_hours": "REAL",
        "sleep_hours": "REAL",
        "social_hours": "REAL",
        "notes": "TEXT",
    })
    add_missing_columns(conn, "typing_tests", {
        "characters": "INTEGER DEFAULT 0",
        "test_date": "TEXT",
    })
    conn.commit()
    conn.close()


def signed_in_user_id():
    return session.get("user_id")


def login_required():
    if not signed_in_user_id():
        return jsonify({"error": "Please log in first."}), 401
    return None


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "project": "BloomWell",
        "status": "Backend running",
        "message": "BloomWell Flask API is ready."
    })


@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")

    if not name or not email or not password:
        return jsonify({"error": "Name, email, and password are required."}), 400
    if "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "Enter a valid email address."}), 400
    if not isinstance(password, str) or len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            (name, email, generate_password_hash(password))
        )
        conn.commit()
        return jsonify({"message": "Account created. Please log in."}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "That email is already registered. Please log in."}), 409
    finally:
        conn.close()


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")

    conn = get_db()
    user = conn.execute(
        "SELECT id, name, email, password_hash FROM users WHERE email = ?",
        (email,)
    ).fetchone()
    conn.close()

    if not user or not isinstance(password, str) or not check_password_hash(
        user["password_hash"], password
    ):
        return jsonify({"error": "Invalid email or password."}), 401

    session.clear()
    session["user_id"] = user["id"]
    return jsonify({
        "message": "Login successful.",
        "user": {"id": user["id"], "name": user["name"], "email": user["email"]}
    })


@app.route("/api/me", methods=["GET"])
def me():
    user_id = signed_in_user_id()
    if not user_id:
        return jsonify({"error": "Not logged in."}), 401

    conn = get_db()
    user = conn.execute(
        "SELECT id, name, email FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()

    if not user:
        session.clear()
        return jsonify({"error": "Account not found."}), 401

    return jsonify({"user": dict(user)})


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out successfully."})


@app.route("/api/activity", methods=["GET", "POST"])
def activity():
    auth_error = login_required()
    if auth_error:
        return auth_error
    user_id = signed_in_user_id()

    if request.method == "GET":
        conn = get_db()
        rows = conn.execute("""
            SELECT id,
                   COALESCE(activity_date, date(created_at)) AS date,
                   COALESCE(screen_hours, 0) AS screen,
                   COALESCE(sleep_hours, 0) AS sleep,
                   COALESCE(social_hours, 0) AS social,
                   COALESCE(notes, '') AS notes
            FROM activity_logs
            WHERE user_id = ?
            ORDER BY id DESC
        """, (user_id,)).fetchall()
        conn.close()
        return jsonify({"activities": [dict(row) for row in rows]})

    data = request.get_json(silent=True) or {}
    activity_date = str(data.get("date", "")).strip()
    notes = str(data.get("notes", "")).strip()
    try:
        screen = float(data["screen"])
        sleep = float(data["sleep"])
        social = float(data["social"])
        if not activity_date:
            raise ValueError
        date.fromisoformat(activity_date)
        if any(v < 0 or v > 24 for v in (screen, sleep, social)):
            raise ValueError
        if social > screen:
            return jsonify({"error": "Social media time cannot exceed screen time."}), 400
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Enter a valid date and hours between 0 and 24."}), 400

    conn = get_db()
    conn.execute("""
        INSERT INTO activity_logs
            (user_id, activity_name, duration_minutes, activity_date,
             screen_hours, sleep_hours, social_hours, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, "daily", screen * 60, activity_date, screen, sleep, social, notes))
    conn.commit()
    conn.close()
    return jsonify({"message": "Activity saved successfully."}), 201


@app.route("/api/typing", methods=["GET", "POST"])
def typing():
    auth_error = login_required()
    if auth_error:
        return auth_error
    user_id = signed_in_user_id()

    if request.method == "GET":
        conn = get_db()
        rows = conn.execute("""
            SELECT id, wpm, accuracy, COALESCE(characters, 0) AS characters,
                   COALESCE(test_date, date(created_at)) AS date
            FROM typing_tests
            WHERE user_id = ?
            ORDER BY id DESC
        """, (user_id,)).fetchall()
        conn.close()
        return jsonify({"tests": [dict(row) for row in rows]})

    data = request.get_json(silent=True) or {}
    try:
        wpm = float(data["wpm"])
        accuracy = float(data["accuracy"])
        characters = int(data.get("characters", 0))
        test_date = str(data.get("date", date.today().isoformat()))
        date.fromisoformat(test_date)
        if wpm < 0 or not 0 <= accuracy <= 100 or characters < 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Invalid typing test results."}), 400

    conn = get_db()
    conn.execute("""
        INSERT INTO typing_tests (user_id, wpm, accuracy, characters, test_date)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, wpm, accuracy, characters, test_date))
    conn.commit()
    conn.close()
    return jsonify({"message": "Typing result saved successfully."}), 201


@app.route("/api/mood", methods=["GET", "POST"])
def mood():
    auth_error = login_required()
    if auth_error:
        return auth_error
    user_id = signed_in_user_id()

    if request.method == "GET":
        conn = get_db()
        row = conn.execute("""
            SELECT mood, notes FROM mood_entries
            WHERE user_id = ? ORDER BY id DESC LIMIT 1
        """, (user_id,)).fetchone()
        conn.close()
        return jsonify({"mood": row["mood"] if row else ""})

    data = request.get_json(silent=True) or {}
    mood_value = str(data.get("mood", "")).strip()
    notes = str(data.get("notes", "")).strip()
    if not mood_value or len(mood_value) > 100:
        return jsonify({"error": "Please choose a valid mood."}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO mood_entries (user_id, mood, notes) VALUES (?, ?, ?)",
        (user_id, mood_value, notes)
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Mood saved successfully."}), 201


@app.route("/api/insights", methods=["GET"])
def insights():
    auth_error = login_required()
    if auth_error:
        return auth_error
    user_id = signed_in_user_id()

    conn = get_db()
    typing_stats = conn.execute("""
        SELECT COUNT(*) AS tests, AVG(wpm) AS average_wpm,
               AVG(accuracy) AS average_accuracy
        FROM typing_tests WHERE user_id = ?
    """, (user_id,)).fetchone()
    activity_stats = conn.execute("""
        SELECT COALESCE(SUM(screen_hours), 0) AS screen_hours,
               COALESCE(SUM(sleep_hours), 0) AS sleep_hours,
               COALESCE(SUM(social_hours), 0) AS social_hours
        FROM activity_logs WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()

    return jsonify({
        "typing_tests": typing_stats["tests"],
        "average_wpm": typing_stats["average_wpm"],
        "average_accuracy": typing_stats["average_accuracy"],
        "screen_hours_total": activity_stats["screen_hours"],
        "sleep_hours_total": activity_stats["sleep_hours"],
        "social_hours_total": activity_stats["social_hours"],
    })


init_db()

if __name__ == "__main__":
    # Debug mode is for local development only. Do not use it in production.
    app.run(host="127.0.0.1", port=5000, debug=True)
