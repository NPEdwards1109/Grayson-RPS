import random

OPTIONS = ["rock", "paper", "scissors"]

NUMBER_TO_CHOICE = {"1": "rock", "2": "paper", "3": "scissors"}


def parse_user_choice(raw: str) -> str | None:
    key = raw.strip().lower()
    if key in NUMBER_TO_CHOICE:
        return NUMBER_TO_CHOICE[key]
    if key in OPTIONS:
        return key
    return None


def judge(user: str, opponent: str) -> str:
    """Return 'win' if user beats opponent, 'loss' if opponent wins, else 'tie'."""
    if user == opponent:
        return "tie"
    if (
        (user == "rock" and opponent == "scissors")
        or (user == "paper" and opponent == "rock")
        or (user == "scissors" and opponent == "paper")
    ):
        return "win"
    return "loss"


def judge_two_player(player1: str, player2: str) -> str:
    """Return 'player1', 'player2', or 'tie'."""
    r = judge(player1, player2)
    if r == "tie":
        return "tie"
    return "player1" if r == "win" else "player2"


def random_computer_choice() -> str:
    return random.choice(OPTIONS)
