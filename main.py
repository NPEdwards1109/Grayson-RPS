import random

options = ["rock", "paper", "scissors"]

NUMBER_TO_CHOICE = {"1": "rock", "2": "paper", "3": "scissors"}


def parse_user_choice(raw: str) -> str | None:
    key = raw.strip().lower()
    if key in NUMBER_TO_CHOICE:
        return NUMBER_TO_CHOICE[key]
    if key in options:
        return key
    return None


computer_choice = random.choice(options)

while True:
    entered = input("Enter a choice — 1 rock, 2 paper, 3 scissors (or type the name): ")
    user_choice = parse_user_choice(entered)
    if user_choice is not None:
        break
    print("Invalid choice. Use 1, 2, 3, or rock / paper / scissors.")

print(f"You chose {user_choice}, computer chose {computer_choice}")

if user_choice == computer_choice:
    print("It's a tie!")
elif user_choice == "rock" and computer_choice == "scissors":
    print("You won!")
elif user_choice == "paper" and computer_choice == "rock":
    print("You won!")
elif user_choice == "scissors" and computer_choice == "paper":
    print("You won!")
else:
    print("You lost!")
