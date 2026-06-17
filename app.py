import hmac
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from functools import wraps

from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SQLITE_PATH = os.path.join(DATA_DIR, "ps_plus.db")
CATEGORIES = ("Essential", "Extra", "Deluxe")
MONTH_NUMBERS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-no-render")
DATABASE_READY = False


def database_url():
    return os.environ.get("DATABASE_URL") or f"sqlite:///{SQLITE_PATH}"


def is_postgres():
    return database_url().startswith(("postgres://", "postgresql://"))


def connect():
    if is_postgres():
        import psycopg
        from psycopg.rows import dict_row

        url = database_url().replace("postgres://", "postgresql://", 1)
        return psycopg.connect(url, row_factory=dict_row)

    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def sql(query):
    return query.replace("?", "%s") if is_postgres() else query


def row_to_dict(row):
    return dict(row)


def execute(conn, query, params=()):
    return conn.execute(sql(query), params)


def init_db():
    with connect() as conn:
        if is_postgres():
            execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS games (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL UNIQUE,
                    cover_url TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL CHECK(event_type IN ('Entrada', 'Saida')),
                    category TEXT NOT NULL DEFAULT '',
                    period TEXT NOT NULL,
                    event_year INTEGER NOT NULL DEFAULT 0,
                    event_month INTEGER NOT NULL DEFAULT 0,
                    event_date TEXT,
                    notes TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(game_id, event_type, category, period)
                )
                """,
            )
        else:
            execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL UNIQUE,
                    cover_url TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )

        if is_postgres():
            execute(conn, "ALTER TABLE games ADD COLUMN IF NOT EXISTS cover_url TEXT NOT NULL DEFAULT ''")
            execute(conn, "ALTER TABLE events ADD COLUMN IF NOT EXISTS event_year INTEGER NOT NULL DEFAULT 0")
            execute(conn, "ALTER TABLE events ADD COLUMN IF NOT EXISTS event_month INTEGER NOT NULL DEFAULT 0")
        else:
            columns = execute(conn, "PRAGMA table_info(games)").fetchall()
            if "cover_url" not in {column["name"] for column in columns}:
                execute(conn, "ALTER TABLE games ADD COLUMN cover_url TEXT NOT NULL DEFAULT ''")
            execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('Entrada', 'Saida')),
                    category TEXT NOT NULL DEFAULT '',
                    period TEXT NOT NULL,
                    event_year INTEGER NOT NULL DEFAULT 0,
                    event_month INTEGER NOT NULL DEFAULT 0,
                    event_date TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE,
                    UNIQUE(game_id, event_type, category, period)
                )
                """,
            )
            event_columns = execute(conn, "PRAGMA table_info(events)").fetchall()
            event_column_names = {column["name"] for column in event_columns}
            if "event_year" not in event_column_names:
                execute(conn, "ALTER TABLE events ADD COLUMN event_year INTEGER NOT NULL DEFAULT 0")
            if "event_month" not in event_column_names:
                execute(conn, "ALTER TABLE events ADD COLUMN event_month INTEGER NOT NULL DEFAULT 0")

        if is_postgres():
            execute(
                conn,
                """
                UPDATE events
                SET
                    event_year = COALESCE(NULLIF(SUBSTRING(period FROM '(\\d{4})'), '')::INTEGER, 0),
                    event_month = CASE LOWER(SPLIT_PART(period, ' ', 1))
                        WHEN 'janeiro' THEN 1 WHEN 'fevereiro' THEN 2
                        WHEN 'marco' THEN 3 WHEN 'março' THEN 3
                        WHEN 'abril' THEN 4 WHEN 'maio' THEN 5
                        WHEN 'junho' THEN 6 WHEN 'julho' THEN 7
                        WHEN 'agosto' THEN 8 WHEN 'setembro' THEN 9
                        WHEN 'outubro' THEN 10 WHEN 'novembro' THEN 11
                        WHEN 'dezembro' THEN 12 ELSE 0
                    END
                WHERE event_year = 0 OR event_month = 0
                """,
            )
        else:
            missing_periods = execute(
                conn,
                "SELECT id, period FROM events WHERE event_year = 0 OR event_month = 0",
            ).fetchall()
            for event in missing_periods:
                event_year, event_month = period_parts(event["period"])
                execute(
                    conn,
                    "UPDATE events SET event_year = ?, event_month = ? WHERE id = ?",
                    (event_year, event_month, event["id"]),
                )


def normalize_category(value):
    value = (value or "").strip().lower()
    if "essential" in value or "essencial" in value:
        return "Essential"
    if "extra" in value:
        return "Extra"
    if "deluxe" in value or "premium" in value:
        return "Deluxe"
    return ""


def normalize_event_type(value):
    value = (value or "").strip().lower()
    if value in ("saida", "saída", "saiu", "removido", "removed", "leaving") or "sai" in value or "remove" in value:
        return "Saida"
    return "Entrada"


def current_period():
    months = [
        "Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
    ]
    today = date.today()
    return f"{months[today.month - 1]} {today.year}"


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return value


def clean_title(title):
    title = re.sub(r"[\x00-\x1f]", "", title or "")
    title = re.sub(r"^[\-*•\d\.\)\s]+", "", title).strip()
    title = re.sub(r"\s*\[[^\]]+\]\s*$", "", title)
    title = re.sub(
        r"\s*\((?:PS4|PS5|PS1|PS2|PS3|PSP|PS VR2?|PSVR2?|PS4/PS5|PS5/PS4|PS4, PS5|PS5, PS4|PS4 e PS5|PS5 e PS4|Apenas no plano Deluxe)\)\s*$",
        "",
        title,
        flags=re.I,
    )
    title = re.sub(r"\s+", " ", title)
    return title.strip(" -:;").upper()


def period_parts(period):
    match = re.search(r"([A-Za-zÀ-ÿ]+)\s+(\d{4})", period or "", flags=re.I)
    if not match:
        return 0, 0
    return int(match.group(2)), MONTH_NUMBERS.get(match.group(1).lower(), 0)


def split_title_and_notes(line):
    notes = []
    title = line.strip()

    for note in re.findall(r"\[([^\]]+)\]", title):
        notes.append(note)
    title = re.sub(r"\[[^\]]+\]", "", title)

    if "|" in title:
        left, right = title.split("|", 1)
        title = left.strip()
        if right.strip():
            notes.append(right.strip())
    else:
        platform_match = re.search(r"\(([^)]*(?:PS4|PS5|PSVR|PS VR|PSP|PS1|PS2|PS3)[^)]*)\)\s*$", title, flags=re.I)
        if platform_match:
            notes.append(platform_match.group(1).strip())
            title = title[: platform_match.start()].strip()

    return clean_title(title), " | ".join(dict.fromkeys(note for note in notes if note))


def parse_import_text(text):
    events = []
    current_category = ""
    current_event_type = "Entrada"
    current_period_value = current_period()
    months = "JANEIRO|FEVEREIRO|MARCO|MARÇO|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO"

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or set(line) <= {"-", "*"}:
            continue

        month_match = re.match(rf"^({months})\s+(\d{{4}})$", line, flags=re.I)
        if month_match:
            current_period_value = f"{month_match.group(1).title()} {month_match.group(2)}"
            current_category = ""
            current_event_type = "Entrada"
            continue

        category = normalize_category(line)
        line_key = re.sub(r"[^a-zA-ZÀ-ÿ]", "", line).lower()
        if category and len(line.split()) <= 3:
            current_category = category
            current_event_type = "Entrada"
            continue
        if "sairamdocatalogo" in line_key or "sairamdocatálogo" in line_key or "sairam" == line_key:
            current_category = ""
            current_event_type = "Saida"
            continue

        title, notes = split_title_and_notes(line)
        if not title or title.upper() == "PS PLUS DELUXE":
            continue

        removal_date = re.search(r"\[([^\]]+)\]", line)
        events.append(
            {
                "title": title,
                "event_type": current_event_type,
                "category": current_category if current_event_type == "Entrada" else "",
                "period": current_period_value,
                "event_date": removal_date.group(1) if removal_date and current_event_type == "Saida" else "",
                "notes": notes,
            }
        )
    return events


def decode_bytes(raw):
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def prepare_event(payload):
    title = clean_title(payload.get("title"))
    event_type = normalize_event_type(payload.get("event_type"))
    category = normalize_category(payload.get("category")) if event_type == "Entrada" else ""
    period = (payload.get("period") or current_period()).strip()
    event_date = parse_date(payload.get("event_date"))
    notes = (payload.get("notes") or "").strip()

    if not title:
        raise ValueError("Informe o nome do jogo.")
    if event_type == "Entrada" and not category:
        raise ValueError("Informe Essential, Extra ou Deluxe para uma entrada.")
    if not period:
        raise ValueError("Informe o mes/ano do evento.")

    cover_url = (payload.get("cover_url") or "").strip()
    event_year, event_month = period_parts(period)
    return title, event_type, category, period, event_date, notes, cover_url, event_year, event_month


def upsert_event_with_connection(conn, payload):
    title, event_type, category, period, event_date, notes, cover_url, event_year, event_month = prepare_event(payload)

    row = execute(conn, "SELECT id FROM games WHERE UPPER(title) = ? ORDER BY id LIMIT 1", (title,)).fetchone()
    if row:
        game_id = row["id"]
        if cover_url:
            execute(conn, "UPDATE games SET cover_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (cover_url, game_id))
        else:
            execute(conn, "UPDATE games SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (game_id,))
    else:
        execute(conn, "INSERT INTO games (title, cover_url) VALUES (?, ?)", (title, cover_url))
        game_id = execute(conn, "SELECT id FROM games WHERE title = ?", (title,)).fetchone()["id"]

    existing = execute(
        conn,
        """
        SELECT id FROM events
        WHERE game_id = ? AND event_type = ? AND category = ? AND period = ?
        """,
        (game_id, event_type, category, period),
    ).fetchone()
    if existing:
        execute(
            conn,
            "UPDATE events SET event_date = ?, notes = ?, event_year = ?, event_month = ? WHERE id = ?",
            (event_date, notes, event_year, event_month, existing["id"]),
        )
    else:
        execute(
            conn,
            """
            INSERT INTO events (game_id, event_type, category, period, event_year, event_month, event_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (game_id, event_type, category, period, event_year, event_month, event_date, notes),
        )
    return game_id


def upsert_event(payload):
    with connect() as conn:
        return upsert_event_with_connection(conn, payload)


def upsert_events(payloads):
    with connect() as conn:
        game_rows = execute(conn, "SELECT id, title, cover_url FROM games").fetchall()
        games_by_title = {row["title"].upper(): row for row in game_rows}
        event_rows = execute(
            conn,
            "SELECT id, game_id, event_type, category, period, event_year, event_month, event_date, notes FROM events",
        ).fetchall()
        events_by_key = {
            (row["game_id"], row["event_type"], row["category"], row["period"]): row
            for row in event_rows
        }

        for payload in payloads:
            title, event_type, category, period, event_date, notes, cover_url, event_year, event_month = prepare_event(payload)
            game = games_by_title.get(title)

            if game:
                game_id = game["id"]
                if cover_url and game["cover_url"] != cover_url:
                    execute(
                        conn,
                        "UPDATE games SET cover_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (cover_url, game_id),
                    )
            else:
                execute(conn, "INSERT INTO games (title, cover_url) VALUES (?, ?)", (title, cover_url))
                game_id = execute(conn, "SELECT id FROM games WHERE title = ?", (title,)).fetchone()["id"]
                game = {"id": game_id, "title": title, "cover_url": cover_url}
                games_by_title[title] = game

            key = (game_id, event_type, category, period)
            existing = events_by_key.get(key)
            if existing:
                changed = (
                    existing["event_year"] != event_year
                    or existing["event_month"] != event_month
                    or (existing["event_date"] or "") != event_date
                    or (existing["notes"] or "") != notes
                )
                if changed:
                    execute(
                        conn,
                        "UPDATE events SET event_year = ?, event_month = ?, event_date = ?, notes = ? WHERE id = ?",
                        (event_year, event_month, event_date, notes, existing["id"]),
                    )
            else:
                execute(
                    conn,
                    """
                    INSERT INTO events (game_id, event_type, category, period, event_year, event_month, event_date, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (game_id, event_type, category, period, event_year, event_month, event_date, notes),
                )
                events_by_key[key] = {
                    "id": None,
                    "game_id": game_id,
                    "event_type": event_type,
                    "category": category,
                    "period": period,
                    "event_year": event_year,
                    "event_month": event_month,
                    "event_date": event_date,
                    "notes": notes,
                }


def latest_games(filters):
    clauses = []
    params = []
    if filters.get("q"):
        clauses.append("UPPER(g.title) LIKE UPPER(?)")
        params.append(f"%{filters['q']}%")
    if filters.get("category"):
        clauses.append("latest.category = ?")
        params.append(filters["category"])
    if filters.get("status") == "Ativo":
        clauses.append("latest.event_type = 'Entrada'")
    elif filters.get("status") == "Saiu":
        clauses.append("latest.event_type = 'Saida'")

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    sort_map = {
        "title": "g.title",
        "status": "latest.event_type",
        "category": "latest.category",
        "period": "latest.event_year",
    }
    sort_column = sort_map.get(filters.get("sort"), "latest.event_year")
    sort_direction = "DESC" if filters.get("direction") == "desc" else "ASC"
    page = max(int(filters.get("page") or 1), 1)
    per_page = min(max(int(filters.get("per_page") or 25), 5), 100)
    offset = (page - 1) * per_page

    with connect() as conn:
        total = execute(
            conn,
            f"""
            WITH latest AS (
                SELECT e.* FROM events e
                JOIN (SELECT game_id, MAX(id) AS id FROM events GROUP BY game_id) x ON x.id = e.id
            )
            SELECT COUNT(*) AS total FROM games g JOIN latest ON latest.game_id = g.id {where}
            """,
            params,
        ).fetchone()["total"]
        rows = execute(
            conn,
            f"""
            WITH latest AS (
                SELECT e.*
                FROM events e
                JOIN (SELECT game_id, MAX(id) AS id FROM events GROUP BY game_id) x ON x.id = e.id
            )
            SELECT
                g.id,
                UPPER(g.title) AS title,
                g.cover_url,
                latest.id AS event_id,
                latest.event_type,
                CASE WHEN latest.event_type = 'Entrada' THEN 'Ativo' ELSE 'Saiu' END AS status,
                latest.category,
                latest.period,
                latest.event_date,
                latest.notes,
                (SELECT COUNT(*) FROM events h WHERE h.game_id = g.id) AS history_count
            FROM games g
            JOIN latest ON latest.game_id = g.id
            {where}
            ORDER BY
                {sort_column} {sort_direction},
                latest.event_month {sort_direction},
                CASE
                    WHEN latest.event_type = 'Saida' THEN 4
                    WHEN latest.category = 'Essential' THEN 1
                    WHEN latest.category = 'Extra' THEN 2
                    WHEN latest.category = 'Deluxe' THEN 3
                    ELSE 5
                END ASC,
                g.title ASC
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, offset],
        ).fetchall()
    return {
        "items": [row_to_dict(row) for row in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max((total + per_page - 1) // per_page, 1),
    }


def update_event(event_id, payload):
    title, event_type, category, period, event_date, notes, cover_url, event_year, event_month = prepare_event(payload)
    with connect() as conn:
        event = execute(conn, "SELECT game_id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not event:
            raise ValueError("Evento nao encontrado.")
        game_id = event["game_id"]
        duplicate = execute(conn, "SELECT id FROM games WHERE UPPER(title) = ? AND id <> ?", (title, game_id)).fetchone()
        if duplicate:
            raise ValueError("Ja existe outro jogo com esse nome.")
        execute(
            conn,
            "UPDATE games SET title = ?, cover_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, cover_url, game_id),
        )
        execute(
            conn,
            "UPDATE events SET event_type = ?, category = ?, period = ?, event_year = ?, event_month = ?, event_date = ?, notes = ? WHERE id = ?",
            (event_type, category, period, event_year, event_month, event_date, notes, event_id),
        )


def delete_game(game_id):
    with connect() as conn:
        execute(conn, "DELETE FROM events WHERE game_id = ?", (game_id,))
        execute(conn, "DELETE FROM games WHERE id = ?", (game_id,))


def history_for_game(game_id):
    with connect() as conn:
        rows = execute(
            conn,
            """
            SELECT e.*, UPPER(g.title) AS title
            FROM events e
            JOIN games g ON g.id = e.game_id
            WHERE e.game_id = ?
            ORDER BY e.id DESC
            """,
            (game_id,),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def counts():
    with connect() as conn:
        row = execute(
            conn,
            """
            WITH latest AS (
                SELECT e.*
                FROM events e
                JOIN (SELECT game_id, MAX(id) AS id FROM events GROUP BY game_id) x ON x.id = e.id
            )
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN event_type = 'Entrada' THEN 1 ELSE 0 END), 0) AS active,
                COALESCE(SUM(CASE WHEN event_type = 'Saida' THEN 1 ELSE 0 END), 0) AS removed,
                (SELECT COUNT(*) FROM events) AS events
            FROM latest
            """,
        ).fetchone()
    return row_to_dict(row)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_user():
    return os.environ.get("ADMIN_USER", "admin")


def admin_password():
    return os.environ.get("ADMIN_PASSWORD", "admin")


LOGIN_HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Login - PS Plus Tracker</title>
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; font-family: system-ui, sans-serif; background: #f5f7fb; color: #172033; }
    form { width: min(380px, calc(100% - 24px)); background: white; border: 1px solid #dfe5ef; border-radius: 8px; padding: 22px; box-shadow: 0 12px 32px rgba(23,32,51,.08); }
    h1 { margin: 0 0 16px; font-size: 24px; }
    label { display: block; margin: 12px 0 6px; color: #627089; font-weight: 700; font-size: 13px; }
    input, button { width: 100%; min-height: 42px; border-radius: 8px; font: inherit; }
    input { border: 1px solid #dfe5ef; padding: 9px 11px; }
    button { border: 1px solid #1355d8; background: #1355d8; color: white; margin-top: 16px; font-weight: 700; cursor: pointer; }
    p { color: #b72e56; min-height: 20px; }
  </style>
</head>
<body>
  <form method="post">
    <h1>PS Plus Tracker</h1>
    <p>{{ error or "" }}</p>
    <label for="username">Usuario</label>
    <input id="username" name="username" autocomplete="username" required>
    <label for="password">Senha</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Entrar</button>
  </form>
</body>
</html>
"""


INDEX_HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PS Plus Tracker</title>
  <style>
    :root { --bg:#f5f7fb; --panel:#fff; --ink:#172033; --muted:#627089; --line:#dfe5ef; --blue:#1355d8; --teal:#0a7f7b; --rose:#b72e56; --amber:#9b6413; --shadow:0 12px 32px rgba(23,32,51,.08); }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--ink); }
    header { background: #101828; color: white; padding: 20px clamp(16px, 4vw, 42px); }
    .top { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
    h1 { margin: 0; font-size: clamp(24px, 4vw, 36px); letter-spacing: 0; }
    header p { margin: 8px 0 0; color: #cbd5e1; max-width: 840px; }
    header a { color: white; text-decoration: none; border: 1px solid #475467; border-radius: 8px; padding: 8px 12px; }
    main { width: min(1200px, 100%); margin: 0 auto; padding: 20px clamp(12px, 3vw, 28px) 44px; }
    .toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) repeat(5, minmax(110px, 155px)); gap: 10px; margin-bottom: 16px; }
    input, select, textarea, button { font: inherit; border: 1px solid var(--line); border-radius: 8px; min-height: 42px; }
    input, select, textarea { width: 100%; padding: 9px 11px; background: white; color: var(--ink); }
    textarea { min-height: 74px; resize: vertical; }
    button { background: var(--blue); color: white; border-color: var(--blue); padding: 9px 14px; cursor: pointer; font-weight: 700; }
    button.secondary { background: white; color: var(--blue); border-color: #b8c9f4; }
    button.danger { background: #b72e56; color: white; border-color: #b72e56; }
    button:disabled { opacity: .6; cursor: wait; }
    .icon-actions { display: flex; gap: 6px; flex-wrap: nowrap; }
    .icon-button { width: 34px; height: 34px; min-height: 34px; padding: 0; display: inline-grid; place-items: center; background: white; color: var(--blue); border-color: #c8d4eb; }
    .icon-button.danger { color: var(--rose); border-color: #efc3d0; }
    .icon-button svg { width: 17px; height: 17px; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); padding: 16px; }
    h2 { margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }
    label { display: block; color: var(--muted); font-size: 13px; font-weight: 700; margin: 12px 0 6px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
    .stat { background: white; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
    .stat b { display: block; font-size: 24px; line-height: 1; }
    .stat span { color: var(--muted); font-size: 12px; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; background: white; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
    th, td { padding: 11px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; }
    tr:last-child td { border-bottom: 0; }
    small { color: var(--muted); }
    .pill { display: inline-flex; min-width: 78px; justify-content: center; border-radius: 999px; padding: 4px 8px; font-size: 12px; font-weight: 800; border: 1px solid transparent; }
    .Essential { background: #e8f1ff; color: var(--blue); border-color: #c8dafc; }
    .Extra { background: #e6f6f4; color: var(--teal); border-color: #bde4df; }
    .Deluxe { background: #fff4df; color: var(--amber); border-color: #f1d8aa; }
    .Saiu, .Saida { background: #ffe8ef; color: var(--rose); border-color: #f4c2d0; }
    .Ativo, .Entrada { background: #e9f8ee; color: #18723a; border-color: #bfe8cc; }
    .empty { color: var(--muted); text-align: center; padding: 28px; background: white; border: 1px dashed var(--line); border-radius: 8px; }
    .toast { min-height: 24px; margin-top: 10px; color: var(--muted); font-size: 14px; }
    .progress-wrap { display: none; margin-top: 12px; }
    .progress-track { height: 10px; overflow: hidden; background: #e8edf5; border-radius: 5px; }
    .progress-bar { width: 0; height: 100%; background: var(--blue); border-radius: 5px; transition: width .25s ease; }
    .progress-bar.processing { width: 45%; animation: processing 1.2s ease-in-out infinite; }
    .progress-label { display: block; margin-top: 6px; color: var(--muted); font-size: 13px; font-weight: 700; }
    @keyframes processing { 0% { transform: translateX(-100%); } 100% { transform: translateX(230%); } }
    .history { margin-top: 16px; }
    .history-item { border-top: 1px solid var(--line); padding: 10px 0; }
    .game-cell { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 12px; align-items: start; min-width: 300px; }
    .cover { width: 96px; height: 96px; object-fit: contain; border: 1px solid var(--line); border-radius: 4px; background: #f8fafc; cursor: zoom-in; }
    .cover:hover { border-color: var(--blue); box-shadow: 0 4px 14px rgba(19, 85, 216, .16); }
    .cover-placeholder { width: 96px; height: 96px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 4px; background: #edf1f7; color: var(--muted); }
    .game-title { white-space: normal; overflow-wrap: anywhere; line-height: 1.3; }
    .pagination { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 12px; color: var(--muted); font-size: 14px; }
    .pagination-actions { display: flex; gap: 6px; }
    .content-actions { display: flex; justify-content: flex-end; gap: 10px; margin: 0 0 14px; }
    dialog { width: min(520px, calc(100% - 24px)); max-height: calc(100vh - 32px); overflow: auto; border: 1px solid var(--line); border-radius: 8px; padding: 0; color: var(--ink); box-shadow: 0 24px 70px rgba(15, 23, 42, .25); }
    dialog::backdrop { background: rgba(15, 23, 42, .58); }
    .modal-head { position: sticky; top: 0; z-index: 1; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; background: white; border-bottom: 1px solid var(--line); }
    .modal-head h2 { margin: 0; }
    .modal-body { padding: 16px; }
    .modal-close { color: var(--muted); border-color: var(--line); }
    .modal-tabs { display: flex; gap: 4px; padding: 0 16px; border-bottom: 1px solid var(--line); background: white; }
    .modal-tab { min-height: 40px; padding: 8px 12px; border: 0; border-radius: 0; background: transparent; color: var(--muted); border-bottom: 2px solid transparent; }
    .modal-tab.active { color: var(--blue); border-bottom-color: var(--blue); }
    .tab-panel[hidden] { display: none; }
    #coverDialog { width: min(560px, calc(100% - 24px)); }
    .cover-preview { display: block; width: 100%; max-height: calc(100vh - 150px); object-fit: contain; background: #f8fafc; }
    @media (max-width: 900px) {
      .top { align-items: flex-start; }
      .toolbar, .stats, .row { grid-template-columns: 1fr; }
      .content-actions { justify-content: stretch; }
      .content-actions button { flex: 1; }
      table, thead, tbody, tr, th, td { display: block; }
      thead { display: none; }
      tr { border-bottom: 1px solid var(--line); padding: 8px 0; }
      td { border: 0; padding: 6px 10px; }
      td[data-label]::before { content: attr(data-label) ": "; color: var(--muted); font-weight: 800; }
      .game-cell { grid-template-columns: 82px minmax(0, 1fr); min-width: 0; }
      .cover, .cover-placeholder { width: 82px; height: 82px; }
    }
  </style>
  <script src="https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js"></script>
</head>
<body>
  <header>
    <div class="top">
      <div>
        <h1>PS Plus Tracker</h1>
        <p>Cadastre entradas, marque saidas por mes e mantenha o historico dos jogos que voltam ao catalogo.</p>
      </div>
      <a href="/logout">Sair</a>
    </div>
  </header>
  <main>
    <section class="toolbar">
      <input id="search" placeholder="Buscar jogo">
      <select id="filterCategory"><option value="">Todas as categorias</option><option>Essential</option><option>Extra</option><option>Deluxe</option></select>
      <select id="filterStatus"><option value="">Todos</option><option>Ativo</option><option>Saiu</option></select>
      <select id="sort"><option value="period" selected>Ordenar por mes e plano</option><option value="title">Ordenar por nome</option><option value="status">Ordenar por status</option><option value="category">Ordenar por categoria</option></select>
      <select id="direction"><option value="desc" selected>Mais recentes</option><option value="asc">Mais antigos</option></select>
      <select id="perPage"><option value="10">10 por pagina</option><option value="25" selected>25 por pagina</option><option value="50">50 por pagina</option><option value="100">100 por pagina</option></select>
    </section>
    <section class="stats" id="stats"></section>
    <section class="content-actions">
      <button type="button" id="openEvent"><i data-lucide="plus">+</i> Novo evento</button>
      <button type="button" class="secondary" id="openImport"><i data-lucide="upload">↑</i> Importar TXT</button>
    </section>
    <section><div id="tableWrap"></div></section>

    <dialog id="eventDialog">
      <div class="modal-head"><h2 id="eventDialogTitle">Registrar evento</h2><button type="button" class="icon-button modal-close" data-close="eventDialog" title="Fechar" aria-label="Fechar"><i data-lucide="x">×</i></button></div>
      <div class="modal-tabs">
        <button type="button" class="modal-tab active" id="eventTabButton" data-tab="eventTabPanel">Evento</button>
        <button type="button" class="modal-tab" id="historyTabButton" data-tab="historyTabPanel">Historico</button>
      </div>
      <div class="modal-body tab-panel" id="eventTabPanel">
        <form id="eventForm">
          <input type="hidden" id="eventId">
          <label for="title">Jogo</label>
          <input id="title" required placeholder="Ex.: God of War">
          <label for="coverUrl">URL da capa</label>
          <input id="coverUrl" type="url" placeholder="https://.../capa.jpg">
          <div class="row">
            <div><label for="eventType">Evento</label><select id="eventType"><option>Entrada</option><option>Saida</option></select></div>
            <div><label for="category">Categoria</label><select id="category"><option>Essential</option><option>Extra</option><option>Deluxe</option></select></div>
          </div>
          <div class="row">
            <div><label for="period">Mes/Ano</label><input id="period" placeholder="Junho 2026"></div>
            <div><label for="eventDate">Data exata</label><input id="eventDate" type="date"></div>
          </div>
          <label for="notes">Notas</label>
          <textarea id="notes" placeholder="Plataformas, fonte ou observacoes"></textarea>
          <div class="actions"><button id="saveButton" type="submit">Salvar evento</button><button type="button" class="secondary" id="clearForm">Limpar</button><button type="button" class="danger" id="deleteGameButton" hidden>Excluir definitivamente</button></div>
        </form>
      </div>
      <div class="modal-body tab-panel" id="historyTabPanel" hidden><section class="history" id="historyPanel"></section></div>
    </dialog>

    <dialog id="importDialog">
      <div class="modal-head"><h2>Importar TXT</h2><button type="button" class="icon-button modal-close" data-close="importDialog" title="Fechar" aria-label="Fechar"><i data-lucide="x">×</i></button></div>
      <div class="modal-body">
        <form id="importForm">
          <input id="txtFile" type="file" accept=".txt,text/plain">
          <div class="actions"><button id="importButton" type="submit">Importar</button></div>
          <div class="progress-wrap" id="progressWrap" aria-live="polite">
            <div class="progress-track"><div class="progress-bar" id="progressBar"></div></div>
            <span class="progress-label" id="progressLabel">Preparando importacao...</span>
          </div>
          <div class="toast" id="toast"></div>
        </form>
      </div>
    </dialog>

    <dialog id="coverDialog">
      <div class="modal-head"><h2 id="coverDialogTitle">Capa do jogo</h2><button type="button" class="icon-button modal-close" data-close="coverDialog" title="Fechar" aria-label="Fechar"><i data-lucide="x">×</i></button></div>
      <div class="modal-body"><img id="coverPreview" class="cover-preview" alt="Capa ampliada"></div>
    </dialog>

  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let games = [];
    let pageInfo = { page: 1, pages: 1, total: 0 };
    let currentPage = 1;
    let editingGameId = null;
    const defaultPeriod = new Intl.DateTimeFormat('pt-BR', { month: 'long', year: 'numeric' }).format(new Date()).replace(/^./, c => c.toUpperCase());
    $('period').value = defaultPeriod;
    async function api(path, options = {}) {
      const response = await fetch(path, options);
      if (response.redirected) location.href = response.url;
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Erro inesperado');
      return data;
    }
    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]));
    }
    function queryString() {
      const query = new URLSearchParams();
      if ($('search').value) query.set('q', $('search').value);
      if ($('filterCategory').value) query.set('category', $('filterCategory').value);
      if ($('filterStatus').value) query.set('status', $('filterStatus').value);
      query.set('sort', $('sort').value);
      query.set('direction', $('direction').value);
      query.set('page', currentPage);
      query.set('per_page', $('perPage').value);
      return query.toString();
    }
    async function loadGames() {
      const result = await api('/api/games?' + queryString());
      games = result.items;
      pageInfo = result;
      renderStats(await api('/api/stats'));
      renderTable();
    }
    function renderStats(stats) {
      $('stats').innerHTML = [['Jogos', stats.total || 0], ['Ativos', stats.active || 0], ['Sairam', stats.removed || 0], ['Eventos', stats.events || 0]]
        .map(([label, value]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join('');
    }
    function renderTable() {
      if (!games.length) { $('tableWrap').innerHTML = '<div class="empty">Nenhum jogo encontrado.</div>'; return; }
      $('tableWrap').innerHTML = `<table><thead><tr><th>Jogo</th><th>Status</th><th>Categoria</th><th>Mes</th><th>Historico</th><th>Acoes</th></tr></thead><tbody>` +
        games.map((game) => `
          <tr>
            <td data-label="Jogo"><div class="game-cell">${game.cover_url ? `<img class="cover" src="${escapeHtml(game.cover_url)}" alt="Capa de ${escapeHtml(game.title)}" loading="lazy" role="button" tabindex="0" data-cover-url="${escapeHtml(game.cover_url)}" data-cover-title="${escapeHtml(game.title)}" onerror="this.style.display='none';this.nextElementSibling.style.display='grid'">` : ''}<span class="cover-placeholder" style="${game.cover_url ? 'display:none' : ''}"><i data-lucide="image">□</i></span><div><strong class="game-title">${escapeHtml(game.title)}</strong><br><small>${escapeHtml(game.notes || '')}</small></div></div></td>
            <td data-label="Status"><span class="pill ${game.status}">${game.status}</span></td>
            <td data-label="Categoria">${game.category ? `<span class="pill ${game.category}">${game.category}</span>` : '-'}</td>
            <td data-label="Mes">${escapeHtml(game.period || '-')}</td>
            <td data-label="Historico">${game.history_count} evento(s)</td>
            <td data-label="Acoes"><div class="icon-actions"><button class="icon-button" type="button" title="Editar evento atual" aria-label="Editar evento atual" onclick="editCurrent(${game.id})"><i data-lucide="pencil">✎</i></button><button class="icon-button" type="button" title="Registrar entrada" aria-label="Registrar entrada" onclick="markEntry(${game.id})"><i data-lucide="log-in">→</i></button><button class="icon-button" type="button" title="Registrar saida" aria-label="Registrar saida" onclick="markExit(${game.id})"><i data-lucide="log-out">←</i></button><button class="icon-button" type="button" title="Ver historico" aria-label="Ver historico" onclick="showHistory(${game.id})"><i data-lucide="history">↺</i></button></div></td>
          </tr>`).join('') + `</tbody></table><div class="pagination"><span>Pagina ${pageInfo.page} de ${pageInfo.pages} - ${pageInfo.total} jogos</span><div class="pagination-actions"><button class="icon-button" title="Pagina anterior" aria-label="Pagina anterior" ${pageInfo.page <= 1 ? 'disabled' : ''} onclick="changePage(-1)"><i data-lucide="chevron-left">‹</i></button><button class="icon-button" title="Proxima pagina" aria-label="Proxima pagina" ${pageInfo.page >= pageInfo.pages ? 'disabled' : ''} onclick="changePage(1)"><i data-lucide="chevron-right">›</i></button></div></div>`;
      if (window.lucide) lucide.createIcons();
    }
    function activateEventTab(panelId) {
      ['eventTabPanel', 'historyTabPanel'].forEach((id) => {
        $(id).hidden = id !== panelId;
      });
      document.querySelectorAll('.modal-tab').forEach((button) => {
        button.classList.toggle('active', button.dataset.tab === panelId);
      });
    }
    function openEventDialog(panelId = 'eventTabPanel') {
      activateEventTab(panelId);
      if (!$('eventDialog').open) $('eventDialog').showModal();
      if (window.lucide) lucide.createIcons();
    }
    function clearForm() {
      $('eventForm').reset();
      $('eventId').value = '';
      editingGameId = null;
      $('eventType').value = 'Entrada';
      $('category').disabled = false;
      $('period').value = defaultPeriod;
      $('saveButton').textContent = 'Salvar evento';
      $('eventDialogTitle').textContent = 'Registrar evento';
      $('historyTabButton').hidden = true;
      $('deleteGameButton').hidden = true;
      $('historyPanel').innerHTML = '';
      activateEventTab('eventTabPanel');
    }
    function fillFromGame(id, eventType) {
      const game = games.find(item => item.id === id);
      if (!game) return;
      editingGameId = game.id;
      $('title').value = game.title;
      $('coverUrl').value = game.cover_url || '';
      $('eventType').value = eventType;
      $('category').value = game.category || 'Extra';
      $('category').disabled = eventType === 'Saida';
      $('period').value = defaultPeriod;
      $('eventDate').value = '';
      $('notes').value = '';
      $('eventDialogTitle').textContent = game.title;
      $('historyTabButton').hidden = false;
      $('deleteGameButton').hidden = false;
      openEventDialog('eventTabPanel');
    }
    window.editCurrent = function(id) {
      const game = games.find(item => item.id === id);
      if (!game) return;
      editingGameId = game.id;
      $('eventId').value = game.event_id;
      $('title').value = game.title;
      $('coverUrl').value = game.cover_url || '';
      $('eventType').value = game.event_type;
      $('category').value = game.category || 'Extra';
      $('category').disabled = game.event_type === 'Saida';
      $('period').value = game.period || defaultPeriod;
      $('eventDate').value = game.event_date || '';
      $('notes').value = game.notes || '';
      $('saveButton').textContent = 'Salvar alteracoes';
      $('eventDialogTitle').textContent = game.title;
      $('historyTabButton').hidden = false;
      $('deleteGameButton').hidden = false;
      openEventDialog('eventTabPanel');
    }
    window.changePage = function(delta) {
      const next = currentPage + delta;
      if (next < 1 || next > pageInfo.pages) return;
      currentPage = next;
      loadGames();
    }
    function openCoverPreview(image) {
      $('coverDialogTitle').textContent = image.dataset.coverTitle || 'Capa do jogo';
      $('coverPreview').src = image.dataset.coverUrl;
      $('coverPreview').alt = `Capa ampliada de ${image.dataset.coverTitle || 'jogo'}`;
      if (!$('coverDialog').open) $('coverDialog').showModal();
    }
    window.markEntry = (id) => fillFromGame(id, 'Entrada');
    window.markExit = (id) => fillFromGame(id, 'Saida');
    window.showHistory = async function(id) {
      const game = games.find(item => item.id === id);
      editingGameId = id;
      const items = await api('/api/games/' + id + '/events');
      $('eventDialogTitle').textContent = game ? game.title : 'Historico';
      $('historyTabButton').hidden = false;
      $('deleteGameButton').hidden = false;
      $('historyPanel').innerHTML = items.length ? items.map((item) => `
        <div class="history-item">
          <strong>${escapeHtml(item.title)}</strong><br>
          <span class="pill ${item.event_type}">${item.event_type}</span>
          ${item.category ? `<span class="pill ${item.category}">${item.category}</span>` : ''}
          <small>${escapeHtml(item.period)} ${item.event_date ? '- ' + escapeHtml(item.event_date) : ''}</small>
          ${item.notes ? `<br><small>${escapeHtml(item.notes)}</small>` : ''}
        </div>`).join('') : '<div class="empty">Nenhum evento no historico.</div>';
      openEventDialog('historyTabPanel');
    }
    $('eventType').addEventListener('change', () => { $('category').disabled = $('eventType').value === 'Saida'; });
    $('eventForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const eventId = $('eventId').value;
      await api(eventId ? '/api/events/' + eventId : '/api/events', { method: eventId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        title: $('title').value, cover_url: $('coverUrl').value, event_type: $('eventType').value, category: $('category').value, period: $('period').value, event_date: $('eventDate').value, notes: $('notes').value
      })});
      clearForm();
      $('eventDialog').close();
      await loadGames();
    });
    $('deleteGameButton').addEventListener('click', async () => {
      if (!editingGameId) return;
      const title = $('title').value || $('eventDialogTitle').textContent || 'este jogo';
      const confirmed = confirm(`Excluir definitivamente "${title}" e todo o historico dele? Esta acao nao pode ser desfeita.`);
      if (!confirmed) return;
      await api('/api/games/' + editingGameId, { method: 'DELETE' });
      clearForm();
      $('eventDialog').close();
      await loadGames();
    });
    $('importForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const file = $('txtFile').files[0];
      if (!file) { $('toast').textContent = 'Selecione um arquivo TXT.'; return; }
      const form = new FormData();
      form.append('file', file);
      const button = $('importButton');
      const bar = $('progressBar');
      const wrap = $('progressWrap');
      const label = $('progressLabel');
      button.disabled = true;
      button.textContent = 'Importando...';
      $('toast').textContent = '';
      wrap.style.display = 'block';
      bar.className = 'progress-bar';
      bar.style.width = '0%';
      bar.style.background = '';

      try {
        const result = await new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open('POST', '/api/import/parse');
          xhr.upload.addEventListener('progress', (progress) => {
            if (!progress.lengthComputable) return;
            const percent = Math.round((progress.loaded / progress.total) * 100);
            bar.style.width = `${Math.min(percent, 100)}%`;
            label.textContent = `Enviando arquivo: ${percent}%`;
          });
          xhr.upload.addEventListener('load', () => {
            bar.style.width = '';
            bar.className = 'progress-bar processing';
            label.textContent = 'Arquivo enviado. Analisando jogos...';
          });
          xhr.addEventListener('load', () => {
            let data = {};
            try { data = JSON.parse(xhr.responseText); } catch (_) {}
            if (xhr.status >= 200 && xhr.status < 300) resolve(data);
            else {
              const serverText = String(xhr.responseText || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 240);
              reject(new Error(data.error || `Erro HTTP ${xhr.status}: ${serverText || 'sem resposta do servidor'}`));
            }
          });
          xhr.addEventListener('error', () => reject(new Error('Falha de conexao durante a importacao.')));
          xhr.send(form);
        });
        const parsedEvents = result.events || [];
        const batchSize = 100;
        let processed = 0;
        bar.className = 'progress-bar';
        bar.style.width = '0%';
        for (let index = 0; index < parsedEvents.length; index += batchSize) {
          const batch = parsedEvents.slice(index, index + batchSize);
          await api('/api/import/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ events: batch }),
          });
          processed += batch.length;
          const percent = parsedEvents.length ? Math.round((processed / parsedEvents.length) * 100) : 100;
          bar.style.width = `${percent}%`;
          label.textContent = `Gravando no banco: ${processed} de ${parsedEvents.length} (${percent}%)`;
        }
        bar.style.width = '100%';
        label.textContent = 'Importacao concluida.';
        $('toast').textContent = `${processed} eventos importados ou atualizados.`;
        $('txtFile').value = '';
        await loadGames();
      } catch (error) {
        bar.className = 'progress-bar';
        bar.style.width = '100%';
        bar.style.background = 'var(--rose)';
        label.textContent = 'A importacao nao foi concluida.';
        $('toast').textContent = error.message;
      } finally {
        button.disabled = false;
        button.textContent = 'Importar';
      }
    });
    ['search', 'filterCategory', 'filterStatus', 'sort', 'direction', 'perPage'].forEach((id) => {
      $(id).addEventListener('input', () => { currentPage = 1; loadGames(); });
      $(id).addEventListener('change', () => { currentPage = 1; loadGames(); });
    });
    document.querySelectorAll('[data-tab]').forEach((button) => {
      button.addEventListener('click', () => activateEventTab(button.dataset.tab));
    });
    document.querySelectorAll('[data-close]').forEach((button) => {
      button.addEventListener('click', () => $(button.dataset.close).close());
    });
    $('tableWrap').addEventListener('click', (event) => {
      const image = event.target.closest('[data-cover-url]');
      if (image) openCoverPreview(image);
    });
    $('tableWrap').addEventListener('keydown', (event) => {
      const image = event.target.closest('[data-cover-url]');
      if (image && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        openCoverPreview(image);
      }
    });
    $('openEvent').addEventListener('click', () => { clearForm(); openEventDialog('eventTabPanel'); });
    $('openImport').addEventListener('click', () => {
      if (!$('importDialog').open) $('importDialog').showModal();
      if (window.lucide) lucide.createIcons();
    });
    $('clearForm').addEventListener('click', clearForm);
    clearForm();
    if (window.lucide) lucide.createIcons();
    loadGames();
  </script>
</body>
</html>
"""


@app.before_request
def ensure_database():
    global DATABASE_READY
    if not DATABASE_READY:
        init_db()
        DATABASE_READY = True


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    app.logger.exception("Erro inesperado na aplicacao")
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Erro interno: {type(exc).__name__}: {exc}"}), 500
    return f"Erro interno: {type(exc).__name__}: {exc}", 500


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        ok_user = hmac.compare_digest(request.form.get("username", ""), admin_user())
        ok_password = hmac.compare_digest(request.form.get("password", ""), admin_password())
        if ok_user and ok_password:
            session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Usuario ou senha invalidos."
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template_string(INDEX_HTML)


@app.route("/api/games")
@login_required
def api_games():
    filters = {
        "q": request.args.get("q", "").strip(),
        "category": normalize_category(request.args.get("category", "")),
        "status": request.args.get("status", "").strip(),
        "sort": request.args.get("sort", "period").strip(),
        "direction": request.args.get("direction", "desc").strip(),
        "page": request.args.get("page", "1"),
        "per_page": request.args.get("per_page", "25"),
    }
    return jsonify(latest_games(filters))


@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(counts())


@app.route("/api/games/<int:game_id>/events")
@login_required
def api_game_events(game_id):
    return jsonify(history_for_game(game_id))


@app.route("/api/games/<int:game_id>", methods=["DELETE"])
@login_required
def api_delete_game(game_id):
    delete_game(game_id)
    return jsonify({"ok": True})


@app.route("/api/events", methods=["POST"])
@login_required
def api_events():
    try:
        game_id = upsert_event(request.get_json(force=True))
        return jsonify({"ok": True, "game_id": game_id}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/events/<int:event_id>", methods=["PUT"])
@login_required
def api_update_event(event_id):
    try:
        update_event(event_id, request.get_json(force=True))
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/import", methods=["POST"])
@login_required
def api_import():
    try:
        file_item = request.files.get("file")
        if not file_item:
            return jsonify({"error": "Arquivo TXT nao enviado."}), 400
        events = parse_import_text(decode_bytes(file_item.read()))
        upsert_events(events)
        return jsonify({"imported": len(events)})
    except Exception as exc:
        app.logger.exception("Falha ao importar TXT")
        return jsonify({"error": f"Falha no banco: {exc}"}), 500


@app.route("/api/import/parse", methods=["POST"])
@login_required
def api_import_parse():
    file_item = request.files.get("file")
    if not file_item:
        return jsonify({"error": "Arquivo TXT nao enviado."}), 400
    events = parse_import_text(decode_bytes(file_item.read()))
    return jsonify({"events": events, "total": len(events)})


@app.route("/api/import/batch", methods=["POST"])
@login_required
def api_import_batch():
    payload = request.get_json(force=True) or {}
    events = payload.get("events") or []
    if not isinstance(events, list) or len(events) > 200:
        return jsonify({"error": "Lote de importacao invalido."}), 400
    upsert_events(events)
    return jsonify({"processed": len(events)})


def import_file(path):
    init_db()
    with open(path, "rb") as file:
        events = parse_import_text(decode_bytes(file.read()))
    upsert_events(events)
    print(f"{len(events)} eventos importados ou atualizados.")
    print(f"Banco: {database_url()}")


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--import":
        import_file(sys.argv[2])
        return
    init_db()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
