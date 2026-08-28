from __future__ import annotations

import json
import os
import random
import sqlite3
from pathlib import Path

import flask
from dash import Dash, Input, Output, callback, ctx, dcc, html, no_update


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("BLIND_LUNCH_DB", BASE_DIR / "blind_lunch.db"))
ADMIN_USERNAME = "cakol"

TEST_USERS = [
    "anna.mueller",
    "benjamin.keller",
    "carla.rossi",
    "daniel.meier",
    "elena.frei",
    "fabian.schmid",
    "giulia.kunz",
]


def connection() -> sqlite3.Connection:
    con = sqlite3.connect(DATABASE_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def get_credentials(request: flask.Request) -> dict:
    """Return the Posit Connect user and groups from the incoming request."""
    credential_header = request.headers.get("RStudio-Connect-Credentials")
    if not credential_header:
        return {"user": "default"}
    try:
        credentials = json.loads(credential_header)
    except (json.JSONDecodeError, TypeError):
        return {"user": "default"}
    return credentials if isinstance(credentials, dict) else {"user": "default"}


def current_username() -> str:
    """Read the authenticated username, with an explicit local-test override."""
    local_user = os.environ.get("BLIND_LUNCH_LOCAL_USER")
    if local_user:
        return local_user.strip().lower()
    if not flask.has_request_context():
        return "default"
    session_user = get_credentials(flask.request)
    return str(session_user.get("user") or "default").strip().lower()


def is_admin(username: str) -> bool:
    return username.lower() == ADMIN_USERNAME


def init_database() -> None:
    with connection() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS registrations (
                username TEXT PRIMARY KEY REFERENCES users(username),
                registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lunch_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS matches (
                round_id INTEGER NOT NULL REFERENCES lunch_rounds(id),
                group_number INTEGER NOT NULL,
                username TEXT NOT NULL REFERENCES users(username),
                is_organizer INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (round_id, username)
            );
            """
        )
        existing_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(users)").fetchall()
        }
        # Migrate databases created by earlier versions of the prototype.
        if "display_name" in existing_columns:
            con.execute("ALTER TABLE users DROP COLUMN display_name")
        if "is_admin" in existing_columns:
            con.execute("ALTER TABLE users DROP COLUMN is_admin")
        con.execute(
            "INSERT OR IGNORE INTO users(username) VALUES (?)",
            (ADMIN_USERNAME,),
        )
        if os.environ.get("BLIND_LUNCH_SEED_TEST_DATA") == "1":
            con.executemany(
                "INSERT OR IGNORE INTO users(username) VALUES (?)",
                [(username,) for username in TEST_USERS],
            )
            con.executemany(
                "INSERT OR IGNORE INTO registrations(username) VALUES (?)",
                [(username,) for username in TEST_USERS[:5]],
            )


def ensure_user(username: str) -> None:
    with connection() as con:
        con.execute(
            "INSERT OR IGNORE INTO users(username) VALUES (?)",
            (username,),
        )


def register(username: str) -> bool:
    ensure_user(username)
    with connection() as con:
        cursor = con.execute(
            "INSERT OR IGNORE INTO registrations(username) VALUES (?)", (username,)
        )
    return cursor.rowcount == 1


def is_registered(username: str) -> bool:
    with connection() as con:
        return (
            con.execute(
                "SELECT 1 FROM registrations WHERE username = ?", (username,)
            ).fetchone()
            is not None
        )


def latest_match(username: str):
    with connection() as con:
        return con.execute(
            """
            SELECT m.round_id, m.group_number, m.is_organizer,
                   GROUP_CONCAT(peers.username, '|||') AS member_names
            FROM matches m
            JOIN matches peers
              ON peers.round_id = m.round_id AND peers.group_number = m.group_number
            WHERE m.username = ?
              AND m.round_id = (SELECT MAX(id) FROM lunch_rounds)
            GROUP BY m.round_id, m.group_number, m.is_organizer
            """,
            (username,),
        ).fetchone()


def generate_matches() -> tuple[int, int]:
    with connection() as con:
        participants = [
            row["username"]
            for row in con.execute(
                "SELECT username FROM registrations ORDER BY username"
            ).fetchall()
        ]
        if len(participants) < 2:
            raise ValueError("Mindestens zwei Anmeldungen sind nötig.")

        random.SystemRandom().shuffle(participants)
        groups: list[list[str]] = []
        if len(participants) % 2:
            groups.append(participants[:3])
            participants = participants[3:]
        groups.extend(participants[index : index + 2] for index in range(0, len(participants), 2))

        round_id = con.execute("INSERT INTO lunch_rounds DEFAULT VALUES").lastrowid
        secure_random = random.SystemRandom()
        for group_number, members in enumerate(groups, start=1):
            organizer = secure_random.choice(members)
            con.executemany(
                "INSERT INTO matches(round_id, group_number, username, is_organizer) VALUES (?, ?, ?, ?)",
                [
                    (round_id, group_number, username, int(username == organizer))
                    for username in members
                ],
            )
    return int(round_id), len(groups)


def participant_count() -> int:
    with connection() as con:
        return con.execute("SELECT COUNT(*) FROM registrations").fetchone()[0]


def participant_names() -> list[str]:
    with connection() as con:
        rows = con.execute(
            "SELECT username FROM registrations ORDER BY username COLLATE NOCASE"
        ).fetchall()
    return [row["username"] for row in rows]


def participant_count_label() -> str:
    count = participant_count()
    return f"{count} {'Person ist' if count == 1 else 'Personen sind'} dabei"


def clear_database(*, clear_users: bool = False) -> None:
    """Clear lunch data in one transaction, optionally removing known users too."""
    with connection() as con:
        con.execute("DELETE FROM matches")
        con.execute("DELETE FROM lunch_rounds")
        con.execute("DELETE FROM registrations")
        con.execute("DELETE FROM sqlite_sequence WHERE name = 'lunch_rounds'")
        if clear_users:
            con.execute("DELETE FROM users")
            con.execute(
                "INSERT INTO users(username) VALUES (?)",
                (ADMIN_USERNAME,),
            )


def latest_teams() -> tuple[int | None, list[dict]]:
    with connection() as con:
        round_row = con.execute("SELECT MAX(id) AS id FROM lunch_rounds").fetchone()
        round_id = round_row["id"]
        if round_id is None:
            return None, []
        rows = con.execute(
            """
            SELECT m.group_number, m.is_organizer, m.username
            FROM matches m
            WHERE m.round_id = ?
            ORDER BY m.group_number, m.username COLLATE NOCASE
            """,
            (round_id,),
        ).fetchall()
    teams: dict[int, list[dict]] = {}
    for row in rows:
        teams.setdefault(row["group_number"], []).append(
            {"username": row["username"], "is_organizer": bool(row["is_organizer"])}
        )
    return int(round_id), [
        {"group_number": number, "members": members}
        for number, members in teams.items()
    ]


init_database()

app = Dash(
    __name__,
    title="Bereich Statistik Blind Lunch",
    suppress_callback_exceptions=True,
)
server = app.server

def serve_layout():
    username = current_username()
    return html.Div(
        className="page-shell",
        children=[
        dcc.Location(id="url"),
        html.Header(
            className="topbar",
            children=[
                html.Div(className="brand-mark", children="SNB"),
                html.Div("Bereich Statistik", className="brand-label"),
            ],
        ),
        html.Main(
            className="content",
            children=[
                html.Section(
                    className="hero",
                    children=[
                        html.P("ZUFÄLLIG ZUSAMMEN. GUT ESSEN.", className="eyebrow"),
                        html.H1("Bereich Statistik Blind Lunch"),
                        html.P(
                            "Lerne Kolleginnen und Kollegen beim Mittagessen neu kennen.",
                            className="intro",
                        ),
                    ],
                ),
                html.Section(
                    className="login-card",
                    children=[
                        html.Div([html.P("ANGEMELDET ALS", className="eyebrow"), html.Strong(username.upper())]),
                        html.P(
                            "Dein Benutzername wird sicher über Posit Connect erkannt.",
                            className="helper",
                        ),
                    ],
                ),
                html.Div(id="view"),
            ],
        ),
        html.Footer("Schweizerische Nationalbank · Blind Lunch"),
        ],
    )


app.layout = serve_layout


def match_card(username: str):
    match = latest_match(username)
    if not match:
        return html.Div(
            className="match-card muted-card",
            children=[
                html.P("DEIN BLIND LUNCH", className="eyebrow"),
                html.H2("Noch etwas Geduld …"),
                html.P("Sobald ausgelost wurde, erscheint dein Lunch-Team genau hier."),
            ],
        )
    names = match["member_names"].split("|||")
    return html.Div(
        className="match-card",
        children=[
            html.P(f"DEIN BLIND LUNCH · GRUPPE {match['group_number']}", className="eyebrow"),
            html.H2(" · ".join(names)),
            html.Div(
                className="organizer-badge" if match["is_organizer"] else "member-badge",
                children=(
                    "🥄 Du hältst den Kochlöffel in der Hand: Bitte organisiere euren Lunch."
                    if match["is_organizer"]
                    else "🍽️ Zurücklehnen erlaubt: Jemand aus deinem Team organisiert."
                ),
            ),
        ],
    )


def participant_dashboard(username: str):
    registered = is_registered(username)
    return html.Section(
        className="dashboard-grid",
        children=[
            html.Div(
                className="action-card",
                children=[
                    html.P("DABEI SEIN", className="eyebrow"),
                    html.H2("Lust auf eine überraschende Mittagspause?"),
                    html.P("Melde dich mit einem Klick für die nächste Auslosung an."),
                    html.Button(
                        "✓ Du bist dabei – Lunch kann kommen!" if registered else "mitmachen",
                        id="join-button",
                        n_clicks=0,
                        disabled=registered,
                        className="confirmed" if registered else "",
                    ),
                    html.Div(id="join-message", className="status-message"),
                ],
            ),
            match_card(username),
        ],
    )


def all_teams_card():
    round_id, teams = latest_teams()
    if not teams:
        body = html.P("Noch keine Teams ausgelost.")
        label = "AKTUELLE AUSLOSUNG"
    else:
        label = f"AKTUELLE AUSLOSUNG · RUNDE {round_id}"
        body = html.Div(
            className="teams-grid",
            children=[
                html.Div(
                    className="team-item",
                    children=[
                        html.H3(f"Team {team['group_number']}"),
                        html.Ul(
                            [
                                html.Li(
                                    [
                                        html.Span(member["username"]),
                                        html.Span(" organisiert", className="team-organizer")
                                        if member["is_organizer"]
                                        else None,
                                    ]
                                )
                                for member in team["members"]
                            ]
                        ),
                    ],
                )
                for team in teams
            ],
        )
    return html.Div(
        className="info-card all-teams-card",
        children=[html.P(label, className="eyebrow"), html.H2("Alle Lunch-Teams"), body],
    )


def admin_dashboard():
    return html.Section(
        className="dashboard-grid",
        children=[
            html.Div(
                className="action-card admin-card",
                children=[
                    html.P("ADMIN-MODUS", className="eyebrow"),
                    html.H2(participant_count_label(), id="participant-count-title"),
                    html.P("Ein Klick erstellt eine neue, zufällige Runde und ersetzt die sichtbare Auslosung."),
                    html.Button("Lunch-Teams auslosen", id="match-button", n_clicks=0),
                    html.Div(id="admin-message", className="status-message"),
                ],
            ),
            html.Div(
                className="info-card",
                children=[
                    html.P("ANMELDUNGEN", className="eyebrow"),
                    html.H3("Wer ist dabei?"),
                    html.P("Zeige die Benutzernamen aller angemeldeten Personen."),
                    html.Button("Namensliste anzeigen", id="signup-list-button", n_clicks=0, className="secondary-button"),
                    html.Div(id="signup-list", className="signup-list", style={"display": "none"}),
                ],
            ),
            html.Div(id="all-teams", children=all_teams_card(), className="all-teams-wrapper"),
        ],
    )


@callback(Output("view", "children"), Input("url", "pathname"))
def render_view(_pathname: str):
    username = current_username()
    ensure_user(username)
    if is_admin(username):
        return html.Div(
            children=[
                dcc.Tabs(
                    id="admin-tabs",
                    value="lunch",
                    className="mode-tabs",
                    children=[
                        dcc.Tab(label="Blind Lunch", value="lunch"),
                        dcc.Tab(label="Admin", value="admin"),
                    ],
                ),
                html.Div(id="admin-tab-content"),
            ]
        )
    return participant_dashboard(username)


@callback(Output("admin-tab-content", "children"), Input("admin-tabs", "value"))
def render_admin_tab(active_tab: str):
    if not is_admin(current_username()):
        return html.P("Kein Zugriff.")
    return admin_dashboard() if active_tab == "admin" else participant_dashboard(current_username())


@callback(
    Output("join-message", "children"),
    Output("join-button", "children"),
    Output("join-button", "disabled"),
    Output("join-button", "className"),
    Input("join-button", "n_clicks"),
    prevent_initial_call=True,
)
def join_lunch(n_clicks: int):
    if not n_clicks:
        return no_update, no_update, no_update, no_update
    username = current_username()
    was_added = register(username)
    message = "🎉 Eingetütet! Wir mischen dich in die nächste Lunch-Lotterie." if was_added else "Du bist bereits dabei."
    return message, "✓ Du bist dabei – Lunch kann kommen!", True, "confirmed"


@callback(
    Output("admin-message", "children"),
    Output("all-teams", "children"),
    Input("match-button", "n_clicks"),
    prevent_initial_call=True,
)
def run_matching(n_clicks: int):
    if not n_clicks or ctx.triggered_id != "match-button":
        return no_update, no_update
    if not is_admin(current_username()):
        return "Diese Aktion ist nur für CAKOL verfügbar.", no_update
    try:
        round_id, group_count = generate_matches()
    except ValueError as error:
        return str(error), no_update
    return f"🎲 Runde {round_id} steht: {group_count} Lunch-Teams wurden ausgelost.", all_teams_card()


@callback(
    Output("signup-list", "children"),
    Output("signup-list", "style"),
    Output("signup-list-button", "children"),
    Input("signup-list-button", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_signup_list(n_clicks: int):
    if not is_admin(current_username()):
        return no_update, no_update, no_update
    if n_clicks % 2 == 0:
        return no_update, {"display": "none"}, "Namensliste anzeigen"
    names = participant_names()
    content = (
        html.Ol([html.Li(name) for name in names])
        if names
        else html.P("Noch niemand hat sich angemeldet.")
    )
    return content, {"display": "block"}, "Namensliste ausblenden"


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8050)
