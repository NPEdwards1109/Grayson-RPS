"""Rock Paper Scissors — Streamlit UI with persistent history."""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from rps_logic import judge, judge_two_player, random_computer_choice
from rps_sounds import play_outcome

HISTORY_PATH = Path(__file__).resolve().parent / "rps_history.json"

CHOICE_LABEL = {"rock": "Rock 🪨", "paper": "Paper 📄", "scissors": "Scissors ✂️"}

MODE_COMPUTER = "vs Computer"
MODE_MULTI = "Two players (same screen)"

CHOICES = ("rock", "paper", "scissors")


def shuffle_choice_order() -> None:
    st.session_state.choice_order = random.sample(list(CHOICES), k=3)


def load_rounds() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        rounds = data.get("rounds", [])
        return rounds if isinstance(rounds, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_rounds(rounds: list[dict]) -> None:
    HISTORY_PATH.write_text(
        json.dumps({"rounds": rounds}, indent=2),
        encoding="utf-8",
    )


def reset_all() -> None:
    st.session_state.rounds = []
    st.session_state.multi_p1_choice = None
    shuffle_choice_order()
    if HISTORY_PATH.exists():
        HISTORY_PATH.unlink()


def init_session() -> None:
    if "rounds" not in st.session_state:
        st.session_state.rounds = load_rounds()
    if "multi_p1_choice" not in st.session_state:
        st.session_state.multi_p1_choice = None


def play_solo(user_choice: str) -> None:
    computer = random_computer_choice()
    outcome = judge(user_choice, computer)
    row = {
        "mode": "solo",
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user_choice,
        "computer": computer,
        "outcome": outcome,
    }
    st.session_state.rounds.append(row)
    save_rounds(st.session_state.rounds)
    shuffle_choice_order()
    play_outcome(outcome)


def play_multi_p1(pick: str) -> None:
    st.session_state.multi_p1_choice = pick
    shuffle_choice_order()


def play_multi_finish(p2_pick: str) -> None:
    p1 = st.session_state.multi_p1_choice
    if p1 is None:
        return
    result = judge_two_player(p1, p2_pick)
    row = {
        "mode": "multi",
        "ts": datetime.now(timezone.utc).isoformat(),
        "p1": p1,
        "p2": p2_pick,
        "result": result,
    }
    st.session_state.rounds.append(row)
    save_rounds(st.session_state.rounds)
    st.session_state.multi_p1_choice = None
    shuffle_choice_order()
    play_outcome(
        {
            "player1": "win",
            "player2": "loss",
            "tie": "tie",
        }[result],
    )


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


def record_caption(rounds: list[dict]) -> str:
    sw, sl, st_ = summarize_solo(rounds)
    m1, m2, mt = summarize_multi(rounds)
    tail = "saved automatically"
    parts: list[str] = []
    if sw + sl + st_ > 0:
        parts.append(f"Solo: **{sw}**W · **{sl}**L · **{st_}**T")
    if m1 + m2 + mt > 0:
        parts.append(f"Two-player: **{m1}**–**{m2}**–**{mt}** (P1 wins · P2 wins · ties)")
    if not parts:
        return f"No rounds yet · {tail}"
    return " · ".join(parts) + f" · {tail}"


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
</style>
""",
        unsafe_allow_html=True,
    )


def render_last_round(last: dict) -> None:
    mode = last.get("mode", "solo")

    if mode == "multi":
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

    solo = df[df["mode"] == "solo"].sort_values("ts")
    multi = df[df["mode"] == "multi"].sort_values("ts")

    if len(solo):
        solo = solo.copy()
        solo["wins"] = (solo["outcome"] == "win").cumsum()
        solo["losses"] = (solo["outcome"] == "loss").cumsum()
        st.caption("Solo — cumulative wins vs losses (you vs computer)")
        st.line_chart(solo.set_index("ts")[["wins", "losses"]])

    if len(multi):
        multi = multi.copy()
        multi["p1_wins"] = (multi["result"] == "player1").cumsum()
        multi["p2_wins"] = (multi["result"] == "player2").cumsum()
        st.caption("Two-player — cumulative wins (player 1 vs player 2)")
        st.line_chart(multi.set_index("ts")[["p1_wins", "p2_wins"]])


def main() -> None:
    st.set_page_config(page_title="Rock Paper Scissors", page_icon="✂️", layout="centered")
    inject_styles()
    init_session()

    rounds = st.session_state.rounds

    st.title("Rock Paper Scissors")
    mode = st.radio(
        "Play as",
        [MODE_COMPUTER, MODE_MULTI],
        horizontal=True,
        key="play_mode",
    )
    if st.session_state.play_mode == MODE_COMPUTER:
        st.session_state.multi_p1_choice = None

    if st.session_state.get("_order_mode") != mode:
        shuffle_choice_order()
        st.session_state._order_mode = mode

    row_cap, row_reset = st.columns([4, 1])
    with row_cap:
        st.caption(record_caption(rounds))
    with row_reset:
        if st.button(
            "Reset",
            use_container_width=True,
            help="Clear all rounds, scores, and saved history",
        ):
            reset_all()
            st.rerun()

    if mode == MODE_MULTI:
        if st.session_state.multi_p1_choice is None:
            st.info("**Player 1** — choose your move.")
        else:
            st.warning("Pass the screen to **Player 2** (no peeking) — then choose a move.")

    if "choice_order" not in st.session_state:
        shuffle_choice_order()

    b1, b2, b3 = st.columns(3)
    keys = st.session_state.choice_order

    for col, key in zip((b1, b2, b3), keys):
        with col:
            if mode == MODE_COMPUTER:
                btn_key = f"solo_{key}"
                if st.button(CHOICE_LABEL[key], use_container_width=True, key=btn_key):
                    play_solo(key)
                    st.rerun()
            elif st.session_state.multi_p1_choice is None:
                btn_key = f"m1_{key}"
                if st.button(CHOICE_LABEL[key], use_container_width=True, key=btn_key):
                    play_multi_p1(key)
                    st.rerun()
            else:
                btn_key = f"m2_{key}"
                if st.button(CHOICE_LABEL[key], use_container_width=True, key=btn_key):
                    play_multi_finish(key)
                    st.rerun()

    if mode == MODE_COMPUTER:
        st.caption("Pick rock, paper, or scissors to play the computer.")
    elif st.session_state.multi_p1_choice is None:
        st.caption("Same device, two people — player 1 picks first, then player 2.")

    if rounds:
        st.divider()
        render_last_round(rounds[-1])
        render_charts(rounds)


if __name__ == "__main__":
    main()
