"""Rock Paper Scissors — Streamlit UI with persistent history."""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from rps_logic import judge, judge_two_player, random_computer_choice
from rps_sounds import play_outcome

CHOICE_LABEL = {"rock": "Rock 🪨", "paper": "Paper 📄", "scissors": "Scissors ✂️"}

MODE_COMPUTER = "vs Computer"
MODE_BO3 = "vs Computer (best of 3)"
MODE_MULTI = "Two players (same screen)"
MODE_MULTI_BO3 = "Two players (best of 3)"

CHOICES = ("rock", "paper", "scissors")

MIN_PLAYER_PIN_LEN = 4
_PIN_PBKDF2_ITERATIONS = 120_000

_APP_DIR = Path(__file__).resolve().parent
DB_PATH = _APP_DIR / "rps.db"
LEGACY_JSON = _APP_DIR / "rps_history.json"


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_users_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            created_at TEXT NOT NULL,
            pin_hash TEXT
        )
        """
    )


def _users_columns(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("PRAGMA table_info(users)")
    return [row[1] for row in cur.fetchall()]


def _ensure_users_pin_hash_column(conn: sqlite3.Connection) -> None:
    cols = _users_columns(conn)
    if "pin_hash" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN pin_hash TEXT")


def _hash_player_pin(pin: str) -> str:
    salt_hex = secrets.token_hex(16)
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt,
        _PIN_PBKDF2_ITERATIONS,
        dklen=32,
    )
    return f"{salt_hex}${dk.hex()}"


def _verify_player_pin(pin: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expect = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt,
        _PIN_PBKDF2_ITERATIONS,
        dklen=len(expect),
    )
    return secrets.compare_digest(dk, expect)


def _create_rounds_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            mode TEXT NOT NULL,
            payload TEXT NOT NULL,
            solo_user_id INTEGER REFERENCES users(id),
            p1_user_id INTEGER REFERENCES users(id),
            p2_user_id INTEGER REFERENCES users(id)
        )
        """
    )


def _rounds_columns(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("PRAGMA table_info(rounds)")
    return [row[1] for row in cur.fetchall()]


def _ensure_user(conn: sqlite3.Connection, username: str) -> int:
    name = username.strip()
    row = conn.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if row:
        return int(row[0])
    conn.execute(
        "INSERT INTO users (username, created_at) VALUES (?, ?)",
        (name, _iso_now()),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _migrate_old_rounds_shape(conn: sqlite3.Connection) -> None:
    legacy = _ensure_user(conn, "Legacy")
    legacy_p2 = _ensure_user(conn, "LegacyP2")
    rows = conn.execute("SELECT payload FROM rounds").fetchall()
    conn.execute("DROP TABLE rounds")
    _create_rounds_table(conn)
    for (payload_str,) in rows:
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        mode = data.get("mode") or "solo"
        ts = data.get("ts") or _iso_now()
        payload_out = json.dumps(data, separators=(",", ":"))
        if mode in ("solo", "solo_bo3"):
            conn.execute(
                """
                INSERT INTO rounds (ts, mode, payload, solo_user_id, p1_user_id, p2_user_id)
                VALUES (?, ?, ?, ?, NULL, NULL)
                """,
                (ts, mode, payload_out, legacy),
            )
        elif mode in ("multi", "multi_bo3"):
            conn.execute(
                """
                INSERT INTO rounds (ts, mode, payload, solo_user_id, p1_user_id, p2_user_id)
                VALUES (?, ?, ?, NULL, ?, ?)
                """,
                (ts, mode, payload_out, legacy, legacy_p2),
            )
        else:
            conn.execute(
                """
                INSERT INTO rounds (ts, mode, payload, solo_user_id, p1_user_id, p2_user_id)
                VALUES (?, ?, ?, ?, NULL, NULL)
                """,
                (ts, mode, payload_out, legacy),
            )


def _migrate_legacy_json_file(conn: sqlite3.Connection) -> None:
    if not LEGACY_JSON.exists():
        return
    try:
        if conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] > 0:
            return
        data = json.loads(LEGACY_JSON.read_text(encoding="utf-8"))
        blob = data.get("rounds", [])
        if not isinstance(blob, list) or not blob:
            return
        legacy = _ensure_user(conn, "Legacy")
        legacy_p2 = _ensure_user(conn, "LegacyP2")
        for data_row in blob:
            if not isinstance(data_row, dict):
                continue
            mode = data_row.get("mode") or "solo"
            ts = data_row.get("ts") or _iso_now()
            payload_out = json.dumps(data_row, separators=(",", ":"))
            if mode in ("solo", "solo_bo3"):
                conn.execute(
                    """
                    INSERT INTO rounds (ts, mode, payload, solo_user_id, p1_user_id, p2_user_id)
                    VALUES (?, ?, ?, ?, NULL, NULL)
                    """,
                    (ts, mode, payload_out, legacy),
                )
            elif mode in ("multi", "multi_bo3"):
                conn.execute(
                    """
                    INSERT INTO rounds (ts, mode, payload, solo_user_id, p1_user_id, p2_user_id)
                    VALUES (?, ?, ?, NULL, ?, ?)
                    """,
                    (ts, mode, payload_out, legacy, legacy_p2),
                )
        conn.commit()
    except (json.JSONDecodeError, OSError, sqlite3.Error):
        pass


def init_db() -> None:
    with _db_connect() as conn:
        _ensure_users_table(conn)
        _ensure_users_pin_hash_column(conn)
        cols: list[str] = []
        try:
            cols = _rounds_columns(conn)
        except sqlite3.OperationalError:
            cols = []
        if not cols:
            _create_rounds_table(conn)
        elif "solo_user_id" not in cols:
            _migrate_old_rounds_shape(conn)
        conn.commit()
    with _db_connect() as conn:
        _migrate_legacy_json_file(conn)


def list_users() -> list[tuple[int, str]]:
    init_db()
    with _db_connect() as conn:
        rows = conn.execute(
            "SELECT id, username FROM users ORDER BY username COLLATE NOCASE ASC",
        ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


def user_requires_pin(user_id: int) -> bool:
    init_db()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT pin_hash FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return bool(row and row[0])


def verify_user_pin(user_id: int, pin: str) -> bool:
    init_db()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT pin_hash FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return False
    stored = row[0]
    if not stored:
        return True
    return _verify_player_pin(pin, str(stored))


def create_user(username: str, pin: str) -> tuple[int, bool]:
    """Register a player. Idempotent on name: returns existing id with created=False.

    New accounts must set a PIN (min MIN_PLAYER_PIN_LEN). Duplicate names never
    accept a PIN from this call — use unlock flow to use an existing account.
    """
    init_db()
    name = username.strip()
    if not name:
        raise ValueError("Username cannot be empty")
    with _db_connect() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if existing:
            conn.commit()
            return int(existing[0]), False
        pin_stripped = pin.strip()
        if len(pin_stripped) < MIN_PLAYER_PIN_LEN:
            raise ValueError(
                f"PIN must be at least {MIN_PLAYER_PIN_LEN} characters (protects your stats).",
            )
        ph = _hash_player_pin(pin_stripped)
        conn.execute(
            "INSERT INTO users (username, created_at, pin_hash) VALUES (?, ?, ?)",
            (name, _iso_now(), ph),
        )
        uid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
    return uid, True


def insert_round(
    payload: dict,
    *,
    solo_user_id: int | None = None,
    p1_user_id: int | None = None,
    p2_user_id: int | None = None,
) -> None:
    init_db()
    mode = str(payload["mode"])
    ts = payload.get("ts") or _iso_now()
    payload_str = json.dumps(payload, separators=(",", ":"))
    with _db_connect() as conn:
        conn.execute(
            """
            INSERT INTO rounds (ts, mode, payload, solo_user_id, p1_user_id, p2_user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ts, mode, payload_str, solo_user_id, p1_user_id, p2_user_id),
        )
        conn.commit()


def load_rounds_for_user(user_id: int) -> list[dict]:
    init_db()
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT payload FROM rounds
            WHERE solo_user_id = ? OR p1_user_id = ? OR p2_user_id = ?
            ORDER BY id ASC
            """,
            (user_id, user_id, user_id),
        ).fetchall()
    out: list[dict] = []
    for (payload_str,) in rows:
        try:
            row = json.loads(payload_str)
            if isinstance(row, dict):
                out.append(row)
        except json.JSONDecodeError:
            continue
    return out


def clear_solo_rounds_for_user(user_id: int) -> None:
    init_db()
    with _db_connect() as conn:
        conn.execute(
            """
            DELETE FROM rounds
            WHERE solo_user_id = ? AND mode IN ('solo', 'solo_bo3')
            """,
            (user_id,),
        )
        conn.commit()


def leaderboard_stats() -> pd.DataFrame:
    init_db()
    agg: dict[int, dict[str, int]] = {}

    def bump(uid: int, *, w: int = 0, l: int = 0, t: int = 0, g: int = 0) -> None:
        d = agg.setdefault(uid, {"wins": 0, "losses": 0, "ties": 0, "games": 0})
        d["wins"] += w
        d["losses"] += l
        d["ties"] += t
        d["games"] += g

    with _db_connect() as conn:
        solo = conn.execute(
            """
            SELECT solo_user_id, payload FROM rounds
            WHERE mode IN ('solo', 'solo_bo3') AND solo_user_id IS NOT NULL
            """,
        ).fetchall()
        multi = conn.execute(
            """
            SELECT p1_user_id, p2_user_id, payload FROM rounds
            WHERE mode IN ('multi', 'multi_bo3')
              AND p1_user_id IS NOT NULL AND p2_user_id IS NOT NULL
            """,
        ).fetchall()
        users_rows = conn.execute("SELECT id, username FROM users").fetchall()

    id_to_name = {int(r[0]): str(r[1]) for r in users_rows}

    for uid, payload_str in solo:
        try:
            data = json.loads(payload_str)
            o = data.get("outcome")
        except json.JSONDecodeError:
            continue
        if o == "win":
            bump(int(uid), w=1, g=1)
        elif o == "loss":
            bump(int(uid), l=1, g=1)
        elif o == "tie":
            bump(int(uid), t=1, g=1)

    for p1, p2, payload_str in multi:
        try:
            data = json.loads(payload_str)
            res = data.get("result")
        except json.JSONDecodeError:
            continue
        p1x, p2x = int(p1), int(p2)
        if res == "player1":
            bump(p1x, w=1, g=1)
            bump(p2x, l=1, g=1)
        elif res == "player2":
            bump(p1x, l=1, g=1)
            bump(p2x, w=1, g=1)
        elif res == "tie":
            bump(p1x, t=1, g=1)
            bump(p2x, t=1, g=1)

    records: list[dict] = []
    for uid, d in sorted(agg.items(), key=lambda x: (-x[1]["wins"], -x[1]["games"])):
        decisive = d["wins"] + d["losses"]
        rate = round(100.0 * d["wins"] / decisive, 1) if decisive else float("nan")
        records.append(
            {
                "Player": id_to_name.get(uid, f"#{uid}"),
                "Wins": d["wins"],
                "Losses": d["losses"],
                "Ties": d["ties"],
                "Games": d["games"],
                "Win %": rate,
            },
        )
    return pd.DataFrame(records)


def shuffle_choice_order() -> None:
    st.session_state.choice_order = random.sample(list(CHOICES), k=3)


def reset_choice_order_fixed() -> None:
    st.session_state.choice_order = list(CHOICES)


def sync_choice_order_to_mode(mode: str) -> None:
    if mode in (MODE_MULTI, MODE_MULTI_BO3):
        shuffle_choice_order()
    else:
        reset_choice_order_fixed()


def reset_all(current_user_id: int | None) -> None:
    st.session_state.multi_p1_choice = None
    st.session_state.bo3_you = 0
    st.session_state.bo3_cpu = 0
    st.session_state.multi_bo3_p1 = 0
    st.session_state.multi_bo3_p2 = 0
    mode = st.session_state.get("play_mode")
    sync_choice_order_to_mode(mode if mode else MODE_COMPUTER)
    if current_user_id is not None:
        clear_solo_rounds_for_user(int(current_user_id))
        st.session_state.rounds = load_rounds_for_user(int(current_user_id))
    else:
        st.session_state.rounds = []
    if LEGACY_JSON.exists():
        LEGACY_JSON.unlink()


def init_session() -> None:
    if "rounds" not in st.session_state:
        st.session_state.rounds = []
    if "rps_pin_verified_ids" not in st.session_state:
        st.session_state.rps_pin_verified_ids = []
    if "multi_p1_choice" not in st.session_state:
        st.session_state.multi_p1_choice = None
    if "bo3_you" not in st.session_state:
        st.session_state.bo3_you = 0
    if "bo3_cpu" not in st.session_state:
        st.session_state.bo3_cpu = 0
    if "multi_bo3_p1" not in st.session_state:
        st.session_state.multi_bo3_p1 = 0
    if "multi_bo3_p2" not in st.session_state:
        st.session_state.multi_bo3_p2 = 0


def _on_playing_as_change() -> None:
    new_id = int(st.session_state.rps_current_user)
    eff = int(st.session_state.rps_effective_user)
    verified = set(st.session_state.get("rps_pin_verified_ids", []))
    if new_id == eff:
        return
    if not user_requires_pin(new_id) or new_id in verified:
        st.session_state.rps_effective_user = new_id
        st.session_state._rps_playing_as_safe = new_id
        return
    safe = int(st.session_state._rps_playing_as_safe)
    st.session_state.rps_pending_pin_for = new_id
    st.session_state.rps_pending_pin_source = "playing_as"
    st.session_state.rps_current_user = safe


def _on_multi_p1_change() -> None:
    new_id = int(st.session_state.rps_multi_p1)
    verified = set(st.session_state.get("rps_pin_verified_ids", []))
    safe = int(st.session_state._rps_multi_p1_safe)
    if not user_requires_pin(new_id) or new_id in verified:
        st.session_state._rps_multi_p1_safe = new_id
        return
    st.session_state.rps_pending_pin_for = new_id
    st.session_state.rps_pending_pin_source = "p1"
    st.session_state.rps_multi_p1 = safe


def _on_multi_p2_change() -> None:
    new_id = int(st.session_state.rps_multi_p2)
    verified = set(st.session_state.get("rps_pin_verified_ids", []))
    safe = int(st.session_state._rps_multi_p2_safe)
    if not user_requires_pin(new_id) or new_id in verified:
        st.session_state._rps_multi_p2_safe = new_id
        return
    st.session_state.rps_pending_pin_for = new_id
    st.session_state.rps_pending_pin_source = "p2"
    st.session_state.rps_multi_p2 = safe


def play_solo(user_choice: str) -> None:
    uid = st.session_state.get("rps_effective_user")
    if uid is None:
        return
    computer = random_computer_choice()
    outcome = judge(user_choice, computer)
    row = {
        "mode": "solo",
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user_choice,
        "computer": computer,
        "outcome": outcome,
    }
    insert_round(row, solo_user_id=int(uid))
    st.session_state.rounds = load_rounds_for_user(int(uid))
    play_outcome(outcome)


def play_solo_bo3(user_choice: str) -> None:
    uid = st.session_state.get("rps_effective_user")
    if uid is None:
        return
    computer = random_computer_choice()
    outcome = judge(user_choice, computer)
    row = {
        "mode": "solo_bo3",
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user_choice,
        "computer": computer,
        "outcome": outcome,
    }
    insert_round(row, solo_user_id=int(uid))
    st.session_state.rounds = load_rounds_for_user(int(uid))
    play_outcome(outcome)

    match_end: str | None = None
    if outcome == "win":
        st.session_state.bo3_you += 1
        if st.session_state.bo3_you >= 2:
            match_end = "you"
    elif outcome == "loss":
        st.session_state.bo3_cpu += 1
        if st.session_state.bo3_cpu >= 2:
            match_end = "cpu"

    if match_end:
        st.session_state.bo3_you = 0
        st.session_state.bo3_cpu = 0
        st.session_state.bo3_match_banner = match_end


def play_multi_p1(pick: str) -> None:
    st.session_state.multi_p1_choice = pick
    shuffle_choice_order()


def play_multi_finish(p2_pick: str) -> None:
    p1 = st.session_state.multi_p1_choice
    if p1 is None:
        return
    p1_uid = st.session_state.get("rps_multi_p1")
    p2_uid = st.session_state.get("rps_multi_p2")
    if p1_uid is None or p2_uid is None or int(p1_uid) == int(p2_uid):
        return
    result = judge_two_player(p1, p2_pick)
    row = {
        "mode": "multi",
        "ts": datetime.now(timezone.utc).isoformat(),
        "p1": p1,
        "p2": p2_pick,
        "result": result,
    }
    insert_round(row, p1_user_id=int(p1_uid), p2_user_id=int(p2_uid))
    cur = st.session_state.get("rps_effective_user")
    if cur is not None:
        st.session_state.rounds = load_rounds_for_user(int(cur))
    st.session_state.multi_p1_choice = None
    shuffle_choice_order()
    play_outcome(
        {
            "player1": "win",
            "player2": "loss",
            "tie": "tie",
        }[result],
    )


def play_multi_finish_bo3(p2_pick: str) -> None:
    p1 = st.session_state.multi_p1_choice
    if p1 is None:
        return
    p1_uid = st.session_state.get("rps_multi_p1")
    p2_uid = st.session_state.get("rps_multi_p2")
    if p1_uid is None or p2_uid is None or int(p1_uid) == int(p2_uid):
        return
    result = judge_two_player(p1, p2_pick)
    row = {
        "mode": "multi_bo3",
        "ts": datetime.now(timezone.utc).isoformat(),
        "p1": p1,
        "p2": p2_pick,
        "result": result,
    }
    insert_round(row, p1_user_id=int(p1_uid), p2_user_id=int(p2_uid))
    cur = st.session_state.get("rps_effective_user")
    if cur is not None:
        st.session_state.rounds = load_rounds_for_user(int(cur))
    st.session_state.multi_p1_choice = None
    shuffle_choice_order()
    play_outcome(
        {
            "player1": "win",
            "player2": "loss",
            "tie": "tie",
        }[result],
    )

    match_end: str | None = None
    if result == "player1":
        st.session_state.multi_bo3_p1 += 1
        if st.session_state.multi_bo3_p1 >= 2:
            match_end = "player1"
    elif result == "player2":
        st.session_state.multi_bo3_p2 += 1
        if st.session_state.multi_bo3_p2 >= 2:
            match_end = "player2"

    if match_end:
        st.session_state.multi_bo3_p1 = 0
        st.session_state.multi_bo3_p2 = 0
        st.session_state.multi_bo3_match_banner = match_end


def summarize_solo(rounds: list[dict]) -> tuple[int, int, int]:
    wins = losses = ties = 0
    for r in rounds:
        if r.get("mode", "solo") != "solo":
            continue
        o = r.get("outcome")
        if o == "win":
            wins += 1
        elif o == "loss":
            losses += 1
        elif o == "tie":
            ties += 1
    return wins, losses, ties


def summarize_solo_bo3(rounds: list[dict]) -> tuple[int, int, int]:
    wins = losses = ties = 0
    for r in rounds:
        if r.get("mode") != "solo_bo3":
            continue
        o = r.get("outcome")
        if o == "win":
            wins += 1
        elif o == "loss":
            losses += 1
        elif o == "tie":
            ties += 1
    return wins, losses, ties


def summarize_multi(rounds: list[dict]) -> tuple[int, int, int]:
    p1w = p2w = ties = 0
    for r in rounds:
        if r.get("mode") != "multi":
            continue
        res = r.get("result")
        if res == "player1":
            p1w += 1
        elif res == "player2":
            p2w += 1
        elif res == "tie":
            ties += 1
    return p1w, p2w, ties


def summarize_multi_bo3(rounds: list[dict]) -> tuple[int, int, int]:
    p1w = p2w = ties = 0
    for r in rounds:
        if r.get("mode") != "multi_bo3":
            continue
        res = r.get("result")
        if res == "player1":
            p1w += 1
        elif res == "player2":
            p2w += 1
        elif res == "tie":
            ties += 1
    return p1w, p2w, ties


def record_caption(rounds: list[dict]) -> str:
    sw, sl, st_ = summarize_solo(rounds)
    b3w, b3l, b3t = summarize_solo_bo3(rounds)
    m1, m2, mt = summarize_multi(rounds)
    mb1, mb2, mbt = summarize_multi_bo3(rounds)
    tail = "each round saved to the database automatically"
    parts: list[str] = []
    if sw + sl + st_ > 0:
        parts.append(f"Solo: **{sw}**W · **{sl}**L · **{st_}**T")
    if b3w + b3l + b3t > 0:
        parts.append(f"Best of 3 (rounds): **{b3w}**W · **{b3l}**L · **{b3t}**T")
    if m1 + m2 + mt > 0:
        parts.append(f"Two-player: **{m1}**–**{m2}**–**{mt}** (P1 wins · P2 wins · ties)")
    if mb1 + mb2 + mbt > 0:
        parts.append(
            f"Two-player Bo3 (rounds): **{mb1}**–**{mb2}**–**{mbt}** (P1 · P2 · ties)"
        )
    if not parts:
        return f"No rounds yet · {tail}"
    return " · ".join(parts) + f" · {tail}"


def _computer_rounds_for_play_mode(rounds: list[dict], play_mode: str) -> list[dict]:
    if play_mode == MODE_COMPUTER:
        return [r for r in rounds if r.get("mode", "solo") == "solo"]
    if play_mode == MODE_BO3:
        return [r for r in rounds if r.get("mode") == "solo_bo3"]
    return []


def _format_play_mode_for_hints(opt: str, hints_on: bool) -> str:
    if not hints_on:
        return opt
    if opt in (MODE_COMPUTER, MODE_BO3):
        return f"📊 {opt}"
    if opt in (MODE_MULTI, MODE_MULTI_BO3):
        return f"👥 {opt}"
    return opt


def render_computer_hint_statistics(rounds: list[dict], play_mode: str) -> None:
    """Win rate, streak, and computer pick mix for solo vs-computer modes."""
    subset = _computer_rounds_for_play_mode(rounds, play_mode)
    with st.container(border=True):
        st.markdown(
            '<p class="rps-hint-chip">💡 Hint statistics</p>',
            unsafe_allow_html=True,
        )
        if not subset:
            st.markdown(
                '<div class="rps-hint-callout rps-hint-callout-info">'
                "Play a round to see <strong>your win rate</strong>, <strong>streak</strong>, "
                "and how often the computer picks rock, paper, or scissors in this mode."
                "</div>",
                unsafe_allow_html=True,
            )
            return

        wins = losses = ties = 0
        cpu_counts = {"rock": 0, "paper": 0, "scissors": 0}
        for r in subset:
            o = r.get("outcome")
            if o == "win":
                wins += 1
            elif o == "loss":
                losses += 1
            elif o == "tie":
                ties += 1
            c = r.get("computer")
            if c in cpu_counts:
                cpu_counts[c] += 1

        n = len(subset)
        decisive = wins + losses
        win_pct = round(100.0 * wins / decisive, 1) if decisive else None

        streak_kind: str | None = None
        streak_n = 0
        for r in reversed(subset):
            o = r.get("outcome")
            if o not in ("win", "loss", "tie"):
                continue
            if streak_kind is None:
                streak_kind = o
                streak_n = 1
            elif o == streak_kind:
                streak_n += 1
            else:
                break
        if streak_kind == "win":
            streak_txt = f"{streak_n} win{'s' if streak_n != 1 else ''}"
        elif streak_kind == "loss":
            streak_txt = f"{streak_n} loss{'es' if streak_n != 1 else ''}"
        elif streak_kind == "tie":
            streak_txt = f"{streak_n} tie{'s' if streak_n != 1 else ''}"
        else:
            streak_txt = "—"

        st.markdown(
            '<p class="rps-hint-callout rps-hint-callout-info" style="margin-top:0">'
            "<strong>Your stats</strong> for this mode — use these to spot streaks and "
            "whether the computer favors one pick.</p>",
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Rounds played", n)
        with c2:
            st.metric(
                "Win rate",
                f"{win_pct}%" if win_pct is not None else "—",
                help="Wins ÷ (wins + losses). Ties are not counted in the rate.",
            )
        with c3:
            st.metric("Current streak", streak_txt)

        short_labels = {"rock": "Rock", "paper": "Paper", "scissors": "Scissors"}
        pick_parts: list[str] = []
        for key in ("rock", "paper", "scissors"):
            cnt = cpu_counts[key]
            pct = round(100.0 * cnt / n, 1) if n else 0.0
            pick_parts.append(
                f"{short_labels[key]} <strong>{pct}%</strong> ({cnt})"
            )
        foot_inner = (
            "Computer picks (all rounds in this mode): "
            + " · ".join(pick_parts)
            + f" · Record <strong>{wins}</strong>W · <strong>{losses}</strong>L · <strong>{ties}</strong>T"
        )
        st.markdown(
            f'<div class="rps-hint-foot">{foot_inner}</div>',
            unsafe_allow_html=True,
        )


def inject_styles() -> None:
    st.markdown(
        """
<style>
.rps-banner {
  text-align: center;
  font-size: clamp(1.75rem, 5vw, 2.25rem);
  font-weight: 800;
  letter-spacing: 0.03em;
  padding: 1rem 1.25rem;
  border-radius: 14px;
  margin: 0.75rem 0 1rem 0;
}
.rps-win {
  background: linear-gradient(180deg, #ecfdf5 0%, #a7f3d0 100%);
  color: #064e3b;
  border: 2px solid #10b981;
}
.rps-loss {
  background: linear-gradient(180deg, #fef2f2 0%, #fecdd3 100%);
  color: #7f1d1d;
  border: 2px solid #f43f5e;
}
.rps-tie {
  background: linear-gradient(180deg, #f8fafc 0%, #e2e8f0 100%);
  color: #1e293b;
  border: 2px solid #64748b;
}
.rps-p2-win {
  background: linear-gradient(180deg, #eff6ff 0%, #bfdbfe 100%);
  color: #1e3a8a;
  border: 2px solid #3b82f6;
}
.rps-picks {
  display: flex;
  justify-content: center;
  gap: 2.5rem;
  flex-wrap: wrap;
  margin-bottom: 1.25rem;
}
.rps-pick {
  text-align: center;
  min-width: 8rem;
}
.rps-pick span {
  display: block;
  font-size: 0.85rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 0.35rem;
}
.rps-pick strong {
  font-size: 1.35rem;
}
.rps-date {
  font-size: 0.7rem;
  color: #94a3b8;
  margin: 0.15rem 0 0 0;
  letter-spacing: 0.02em;
}
/* Hint highlights */
.rps-hint-panel {
  border: 2px solid rgba(251, 191, 36, 0.55);
  background: linear-gradient(145deg, rgba(254, 252, 232, 0.9) 0%, rgba(253, 230, 138, 0.22) 100%);
  border-radius: 12px;
  padding: 0.85rem 1.1rem;
  margin: 0.35rem 0 0.75rem 0;
  box-shadow: 0 1px 3px rgba(245, 158, 11, 0.12);
}
.rps-hint-panel table { margin: 0.5rem 0 0 0; }
.rps-hint-chip {
  margin: 0 0 0.35rem 0;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #b45309;
}
.rps-hint-callout {
  padding: 0.75rem 1rem;
  border-radius: 10px;
  margin: 0.25rem 0 0.65rem 0;
  border-left: 4px solid;
  line-height: 1.45;
}
.rps-hint-callout-info {
  background: rgba(14, 165, 233, 0.1);
  border-left-color: #0284c7;
  color: #0c4a6e;
}
.rps-hint-callout-warn {
  background: rgba(251, 191, 36, 0.18);
  border-left-color: #d97706;
  color: #78350f;
}
.rps-hint-foot {
  margin: 0.5rem 0 0 0;
  padding: 0.55rem 0.85rem;
  background: rgba(100, 116, 139, 0.1);
  border-radius: 8px;
  border-left: 3px solid #64748b;
  font-size: 0.92rem;
  color: #334155;
  line-height: 1.45;
}
.rps-hint-mode-legend {
  font-size: 0.78rem;
  color: #92400e;
  background: rgba(254, 243, 199, 0.55);
  border: 1px solid rgba(251, 191, 36, 0.45);
  border-radius: 8px;
  padding: 0.45rem 0.65rem;
  margin: 0.15rem 0 0.5rem 0;
  line-height: 1.4;
}
</style>
""",
        unsafe_allow_html=True,
    )


def render_last_round(last: dict) -> None:
    mode = last.get("mode", "solo")

    if mode in ("multi", "multi_bo3"):
        result = last["result"]
        p1 = CHOICE_LABEL[last["p1"]]
        p2 = CHOICE_LABEL[last["p2"]]
        if result == "player1":
            st.markdown(
                '<div class="rps-banner rps-win">Player 1 wins</div>',
                unsafe_allow_html=True,
            )
        elif result == "player2":
            st.markdown(
                '<div class="rps-banner rps-p2-win">Player 2 wins</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div class="rps-banner rps-tie">Tie</div>', unsafe_allow_html=True)
        st.markdown(
            f"""
<div class="rps-picks">
  <div class="rps-pick"><span>Player 1</span><strong>{p1}</strong></div>
  <div class="rps-pick"><span>Player 2</span><strong>{p2}</strong></div>
</div>
""",
            unsafe_allow_html=True,
        )
        return

    if mode not in ("solo", "solo_bo3"):
        return

    outcome = last["outcome"]
    you = CHOICE_LABEL[last["user"]]
    cpu = CHOICE_LABEL[last["computer"]]

    if outcome == "win":
        st.markdown('<div class="rps-banner rps-win">You win</div>', unsafe_allow_html=True)
    elif outcome == "loss":
        st.markdown('<div class="rps-banner rps-loss">You lose</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="rps-banner rps-tie">Tie</div>', unsafe_allow_html=True)

    st.markdown(
        f"""
<div class="rps-picks">
  <div class="rps-pick"><span>You</span><strong>{you}</strong></div>
  <div class="rps-pick"><span>Computer</span><strong>{cpu}</strong></div>
</div>
""",
        unsafe_allow_html=True,
    )


def df_normalize_modes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "mode" not in out.columns:
        out["mode"] = "solo"
    else:
        out["mode"] = out["mode"].fillna("solo")
    return out


def render_charts(rounds: list[dict]) -> None:
    df = pd.DataFrame(rounds)
    if df.empty:
        return
    df = df_normalize_modes(df)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    solo = df[df["mode"].isin(["solo", "solo_bo3"])].sort_values("ts")
    multi = df[df["mode"].isin(["multi", "multi_bo3"])].sort_values("ts")

    if len(solo):
        solo = solo.copy()
        solo["wins"] = (solo["outcome"] == "win").cumsum()
        solo["losses"] = (solo["outcome"] == "loss").cumsum()
        st.caption("Solo & best of 3 — cumulative wins vs losses (you vs computer)")
        st.line_chart(solo.set_index("ts")[["wins", "losses"]])

    if len(multi):
        multi = multi.copy()
        multi["p1_wins"] = (multi["result"] == "player1").cumsum()
        multi["p2_wins"] = (multi["result"] == "player2").cumsum()
        st.caption("Two-player & two-player Bo3 — cumulative round wins (P1 vs P2)")
        st.line_chart(multi.set_index("ts")[["p1_wins", "p2_wins"]])


def main() -> None:
    st.set_page_config(page_title="Rock Paper Scissors", page_icon="✂️", layout="centered")
    inject_styles()
    init_session()
    init_db()

    users = list_users()
    if not users:
        st.title("Rock Paper Scissors")
        st.info(
            "Create your first player to start tracking rounds and leaderboard stats. "
            "Choose a PIN so others on this device cannot use your name without it.",
        )
        nu = st.text_input("Display name", max_chars=40, key="rps_first_user_name")
        pin_a = st.text_input(
            "PIN",
            type="password",
            help=f"At least {MIN_PLAYER_PIN_LEN} characters. Not recoverable if forgotten.",
            key="rps_first_user_pin",
        )
        pin_b = st.text_input("Confirm PIN", type="password", key="rps_first_user_pin2")
        if st.button("Create player"):
            name = nu.strip()
            if not name:
                st.error("Enter a display name.")
            elif pin_a != pin_b:
                st.error("PIN and confirmation do not match.")
            else:
                try:
                    uid, created = create_user(name, pin_a)
                    if not created:
                        st.error("That name is already registered — reload and sign in from the sidebar.")
                    else:
                        st.session_state.rps_pin_verified_ids = [uid]
                        st.session_state.rps_effective_user = uid
                        st.session_state._rps_playing_as_safe = uid
                        st.rerun()
                except ValueError as e:
                    st.error(str(e))
        st.stop()

    user_ids = [u[0] for u in users]
    user_names = dict(users)

    first_uid = int(user_ids[0])
    if "rps_effective_user" not in st.session_state:
        co = st.session_state.get("rps_current_user")
        if co is not None and int(co) in user_ids:
            st.session_state.rps_effective_user = int(co)
        else:
            st.session_state.rps_effective_user = first_uid
    eff_uid = int(st.session_state.rps_effective_user)
    if eff_uid not in user_ids:
        st.session_state.rps_effective_user = first_uid
        eff_uid = first_uid
    st.session_state.setdefault("_rps_playing_as_safe", eff_uid)
    cur_sid = st.session_state.get("rps_current_user")
    if cur_sid is None or int(cur_sid) not in user_ids:
        st.session_state.rps_current_user = eff_uid

    with st.sidebar:
        st.markdown("### Players")
        flash = st.session_state.pop("_rps_flash", None)
        if flash:
            level, text = flash
            if level == "success":
                st.success(text)
            elif level == "info":
                st.info(text)
            else:
                st.error(text)

        pend = st.session_state.get("rps_pending_pin_for")
        if pend is not None:
            who = user_names.get(int(pend), f"#{pend}")
            st.warning(f"Enter PIN to use **{who}** this session.")
            st.text_input(
                "PIN",
                type="password",
                key="rps_pin_challenge_value",
            )
            u1, u2 = st.columns(2)
            if u1.button("Unlock", key="rps_pin_unlock_btn"):
                typed = st.session_state.get("rps_pin_challenge_value", "")
                if verify_user_pin(int(pend), typed):
                    st.session_state.rps_pin_verified_ids.append(int(pend))
                    src = st.session_state.pop("rps_pending_pin_source", "playing_as")
                    if src == "playing_as":
                        st.session_state.rps_current_user = int(pend)
                        st.session_state.rps_effective_user = int(pend)
                        st.session_state._rps_playing_as_safe = int(pend)
                    elif src == "p1":
                        st.session_state.rps_multi_p1 = int(pend)
                        st.session_state._rps_multi_p1_safe = int(pend)
                    elif src == "p2":
                        st.session_state.rps_multi_p2 = int(pend)
                        st.session_state._rps_multi_p2_safe = int(pend)
                    st.session_state.pop("rps_pending_pin_for", None)
                    st.rerun()
                else:
                    st.error("Incorrect PIN.")
            if u2.button("Cancel", key="rps_pin_cancel_btn"):
                st.session_state.pop("rps_pending_pin_for", None)
                st.session_state.pop("rps_pending_pin_source", None)
                st.rerun()

        st.selectbox(
            "Playing as",
            options=user_ids,
            format_func=lambda uid: user_names[int(uid)],
            key="rps_current_user",
            on_change=_on_playing_as_change,
        )
        new_player = st.text_input("New player name", max_chars=40, key="rps_new_player_input")
        np_pin = st.text_input(
            "New player PIN",
            type="password",
            help=f"Required for new names ({MIN_PLAYER_PIN_LEN}+ characters). Ignored if the name already exists.",
            key="rps_new_player_pin",
        )
        np_pin2 = st.text_input("Confirm PIN", type="password", key="rps_new_player_pin2")
        if st.button("Add player"):
            name = new_player.strip()
            if not name:
                st.session_state["_rps_flash"] = ("error", "Enter a name.")
            elif np_pin != np_pin2:
                st.session_state["_rps_flash"] = ("error", "PIN and confirmation do not match.")
            else:
                try:
                    uid, created = create_user(name, np_pin)
                    if created:
                        st.session_state.rps_pin_verified_ids.append(uid)
                        st.session_state["_rps_flash"] = (
                            "success",
                            "Player added — unlocked for this browser session.",
                        )
                    else:
                        st.session_state["_rps_flash"] = (
                            "info",
                            "That player already exists. Choose them under Playing as (PIN required if they set one).",
                        )
                except ValueError as e:
                    st.session_state["_rps_flash"] = ("error", str(e))
            st.rerun()
        with st.expander("Lifetime leaderboard"):
            lb_df = leaderboard_stats()
            if lb_df.empty:
                st.caption("No finished games yet.")
            else:
                display_lb = lb_df.copy()
                display_lb["Win %"] = display_lb["Win %"].apply(
                    lambda x: "—" if pd.isna(x) else f"{x:.1f}%",
                )
                st.dataframe(display_lb, hide_index=True, use_container_width=True)

    cur_uid = int(st.session_state.rps_effective_user)
    st.session_state.rounds = load_rounds_for_user(cur_uid)

    rounds = st.session_state.rounds

    banner = st.session_state.pop("bo3_match_banner", None)
    if banner == "you":
        st.success("You won the match — first to 2 wins!")
    elif banner == "cpu":
        st.error("Computer won the match — first to 2 wins!")

    mb_banner = st.session_state.pop("multi_bo3_match_banner", None)
    if mb_banner == "player1":
        st.success("Player 1 won the match — first to 2 wins!")
    elif mb_banner == "player2":
        st.success("Player 2 won the match — first to 2 wins!")

    st.title("Rock Paper Scissors")
    st.markdown(
        '<p class="rps-hint-chip" style="margin-bottom:0.2rem">💡 Hint display</p>',
        unsafe_allow_html=True,
    )
    show_hints = st.checkbox(
        "Show hints",
        value=True,
        key="rps_show_hints",
        help="When on, hint areas use highlighted panels (rules, stats, turn reminders, tips).",
    )
    if show_hints:
        with st.expander("How to play — rules & modes", expanded=False):
            st.markdown(
                """
<div class="rps-hint-panel">

<p class="rps-hint-chip">📖 Rules reference</p>

**Basics:** Rock beats scissors, scissors beats paper, paper beats rock. Same pick = tie.

| Mode | What to know |
|------|----------------|
| **vs Computer** | One round per pick; buttons stay in the same order. With **Show hints** on, you get **win rate**, **streak**, and **computer pick** percentages for this mode. |
| **vs Computer (best of 3)** | First to **2 wins** wins the *match*; **ties don’t** move the match score. Same hint statistics as above (tracked separately from endless solo). |
| **Two players** | Choose **Player 1** and **Player 2** (sidebar players, must differ). Both get **leaderboard** credit. Buttons may **shuffle** between turns. |
| **Two players (best of 3)** | Same flow; first to **2 round wins** wins the match; ties don’t change it. |

**Players & PIN** — new players choose a PIN so others cannot switch to their name or slot them as P1/P2 without unlocking once per browser session. Older accounts without a PIN stay open on this device.

**Database** — each round is saved to **rps.db** automatically. **Reset** — deletes **your** solo / vs-computer history only (two-player rows stay). Removes legacy **rps_history.json** if present.

</div>
""",
                unsafe_allow_html=True,
            )
    if show_hints:
        st.markdown(
            """
<style>
[data-testid="stRadio"] label span:last-child {
  padding: 0.4rem 0.65rem;
  border-radius: 10px;
  transition: background 0.15s ease, box-shadow 0.15s ease;
}
/* First two options: vs computer — stronger highlight (hint statistics apply here) */
[data-testid="stRadio"] label:nth-of-type(1) span:last-child,
[data-testid="stRadio"] label:nth-of-type(2) span:last-child {
  background: linear-gradient(165deg, rgba(255, 251, 235, 0.98) 0%, rgba(254, 243, 199, 0.82) 100%);
  box-shadow:
    inset 0 0 0 1px rgba(245, 158, 11, 0.45),
    0 0 0 1px rgba(251, 146, 60, 0.25),
    0 2px 8px rgba(217, 119, 6, 0.12);
}
[data-testid="stRadio"] label:nth-of-type(1):has(input:checked) span:last-child,
[data-testid="stRadio"] label:nth-of-type(2):has(input:checked) span:last-child {
  background: linear-gradient(165deg, rgba(253, 230, 138, 0.98) 0%, rgba(251, 191, 36, 0.62) 100%);
  box-shadow:
    inset 0 0 0 2px rgba(217, 119, 6, 0.75),
    0 0 0 3px rgba(251, 191, 36, 0.4),
    0 2px 10px rgba(217, 119, 6, 0.22);
}
/* Last two options: two-player — cooler panel */
[data-testid="stRadio"] label:nth-of-type(3) span:last-child,
[data-testid="stRadio"] label:nth-of-type(4) span:last-child {
  background: linear-gradient(165deg, rgba(248, 250, 252, 0.95) 0%, rgba(226, 232, 240, 0.65) 100%);
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.45),
    0 1px 3px rgba(15, 23, 42, 0.06);
}
[data-testid="stRadio"] label:nth-of-type(3):has(input:checked) span:last-child,
[data-testid="stRadio"] label:nth-of-type(4):has(input:checked) span:last-child {
  background: linear-gradient(165deg, rgba(224, 242, 254, 0.95) 0%, rgba(186, 230, 253, 0.65) 100%);
  box-shadow:
    inset 0 0 0 2px rgba(14, 165, 233, 0.5),
    0 0 0 3px rgba(125, 211, 252, 0.35),
    0 2px 8px rgba(14, 165, 233, 0.12);
}
</style>
""",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p class="rps-hint-mode-legend"><strong>Highlighted options:</strong> '
            "<strong>Amber panels</strong> (📊) = hint statistics vs computer · "
            "<strong>Gray/blue panels</strong> (👥) = two-player turn hints only</p>",
            unsafe_allow_html=True,
        )
    mode = st.radio(
        "Play as",
        [MODE_COMPUTER, MODE_BO3, MODE_MULTI, MODE_MULTI_BO3],
        horizontal=True,
        key="play_mode",
        format_func=lambda o: _format_play_mode_for_hints(o, show_hints),
    )
    if st.session_state.play_mode in (MODE_COMPUTER, MODE_BO3):
        st.session_state.multi_p1_choice = None

    if st.session_state.get("_order_mode") != mode:
        sync_choice_order_to_mode(mode)
        st.session_state._order_mode = mode
        st.session_state.bo3_you = 0
        st.session_state.bo3_cpu = 0
        st.session_state.multi_bo3_p1 = 0
        st.session_state.multi_bo3_p2 = 0
        st.session_state.multi_p1_choice = None

    multi_ready = True
    if mode in (MODE_MULTI, MODE_MULTI_BO3):
        if len(user_ids) < 2:
            st.warning("Add at least two players (sidebar) for two-player modes.")
            multi_ready = False
        else:
            try:
                idx1 = user_ids.index(int(cur_uid))
            except ValueError:
                idx1 = 0
            idx2 = (idx1 + 1) % len(user_ids) if len(user_ids) > 1 else idx1
            p1_default = user_ids[min(idx1, len(user_ids) - 1)]
            p2_default = user_ids[min(idx2, len(user_ids) - 1)]
            st.session_state.setdefault("_rps_multi_p1_safe", p1_default)
            st.session_state.setdefault("_rps_multi_p2_safe", p2_default)
            mc1, mc2 = st.columns(2)
            with mc1:
                st.selectbox(
                    "Player 1",
                    options=user_ids,
                    format_func=lambda uid: user_names[int(uid)],
                    index=min(idx1, len(user_ids) - 1),
                    key="rps_multi_p1",
                    on_change=_on_multi_p1_change,
                )
            with mc2:
                st.selectbox(
                    "Player 2",
                    options=user_ids,
                    format_func=lambda uid: user_names[int(uid)],
                    index=min(idx2, len(user_ids) - 1),
                    key="rps_multi_p2",
                    on_change=_on_multi_p2_change,
                )
            if int(st.session_state.rps_multi_p1) == int(st.session_state.rps_multi_p2):
                st.warning("Choose two different players for two-player modes.")
                multi_ready = False

    row_cap, row_reset = st.columns([4, 1])
    with row_cap:
        st.caption(record_caption(rounds))
        st.markdown(
            f'<p class="rps-date">{datetime.now().strftime("%b %d, %Y")}</p>',
            unsafe_allow_html=True,
        )
    with row_reset:
        if st.button(
            "Reset",
            use_container_width=True,
            help=(
                "Clears match counters and deletes your solo / vs-computer rounds from the DB. "
                "Two-player rounds are kept."
            ),
        ):
            reset_all(st.session_state.get("rps_effective_user"))
            st.rerun()

    if mode == MODE_BO3:
        st.caption(
            f"This match: **{st.session_state.bo3_you}**–**{st.session_state.bo3_cpu}** "
            "(first to 2 wins · ties replay)"
        )

    if show_hints and mode in (MODE_COMPUTER, MODE_BO3):
        render_computer_hint_statistics(rounds, mode)

    if mode == MODE_MULTI_BO3:
        st.caption(
            f"This match: **{st.session_state.multi_bo3_p1}**–**{st.session_state.multi_bo3_p2}** "
            "(first to 2 round wins · ties replay)"
        )

    if mode in (MODE_MULTI, MODE_MULTI_BO3) and show_hints:
        if st.session_state.multi_p1_choice is None:
            st.markdown(
                '<div class="rps-hint-callout rps-hint-callout-info">'
                "<strong>Player 1</strong> — choose your move.</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="rps-hint-callout rps-hint-callout-warn">'
                "Pass the screen to <strong>Player 2</strong> (no peeking) — then choose a move.</div>",
                unsafe_allow_html=True,
            )

    if "choice_order" not in st.session_state:
        reset_choice_order_fixed()

    b1, b2, b3 = st.columns(3)
    keys = st.session_state.choice_order

    for col, key in zip((b1, b2, b3), keys):
        with col:
            if mode == MODE_COMPUTER:
                btn_key = f"solo_{key}"
                if st.button(CHOICE_LABEL[key], use_container_width=True, key=btn_key):
                    play_solo(key)
                    st.rerun()
            elif mode == MODE_BO3:
                btn_key = f"bo3_{key}"
                if st.button(CHOICE_LABEL[key], use_container_width=True, key=btn_key):
                    play_solo_bo3(key)
                    st.rerun()
            elif mode == MODE_MULTI:
                dis = not multi_ready
                if st.session_state.multi_p1_choice is None:
                    btn_key = f"m1_{key}"
                    if st.button(
                        CHOICE_LABEL[key],
                        use_container_width=True,
                        key=btn_key,
                        disabled=dis,
                    ):
                        play_multi_p1(key)
                        st.rerun()
                else:
                    btn_key = f"m2_{key}"
                    if st.button(
                        CHOICE_LABEL[key],
                        use_container_width=True,
                        key=btn_key,
                        disabled=dis,
                    ):
                        play_multi_finish(key)
                        st.rerun()
            elif mode == MODE_MULTI_BO3:
                dis = not multi_ready
                if st.session_state.multi_p1_choice is None:
                    btn_key = f"m1b3_{key}"
                    if st.button(
                        CHOICE_LABEL[key],
                        use_container_width=True,
                        key=btn_key,
                        disabled=dis,
                    ):
                        play_multi_p1(key)
                        st.rerun()
                else:
                    btn_key = f"m2b3_{key}"
                    if st.button(
                        CHOICE_LABEL[key],
                        use_container_width=True,
                        key=btn_key,
                        disabled=dis,
                    ):
                        play_multi_finish_bo3(key)
                        st.rerun()

    if show_hints:
        if mode == MODE_COMPUTER:
            st.markdown(
                '<div class="rps-hint-foot">Pick rock, paper, or scissors to play the computer.</div>',
                unsafe_allow_html=True,
            )
        elif mode == MODE_BO3:
            st.markdown(
                '<div class="rps-hint-foot">Win two rounds before the computer does. '
                "Ties do not change the match score.</div>",
                unsafe_allow_html=True,
            )
        elif mode == MODE_MULTI_BO3:
            st.markdown(
                '<div class="rps-hint-foot">First to win two rounds wins the match. '
                "Ties do not change the match score. Same device — player 1 picks first, "
                "then player 2.</div>",
                unsafe_allow_html=True,
            )
        elif mode == MODE_MULTI:
            if st.session_state.multi_p1_choice is None:
                st.markdown(
                    '<div class="rps-hint-foot">Same device, two people — player 1 picks first, '
                    "then player 2.</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="rps-hint-foot">Player 2: pick your move.</div>',
                    unsafe_allow_html=True,
                )

    if rounds:
        st.divider()
        render_last_round(rounds[-1])
        render_charts(rounds)


if __name__ == "__main__":
    main()
