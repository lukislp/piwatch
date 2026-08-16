"""Manual smoke test: sends one real Telegram message using whatever
PIWATCH_ML_TELEGRAM_BOT_TOKEN/_CHAT_ID are set in the environment. Run this
by hand once after setting up a bot (see telegram.py's module docstring) to
confirm the token/chat ID actually work, before wiring alerting into
anything real.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from alerting.telegram import TelegramConfigError, send_message


def main() -> None:
    try:
        send_message("piwatch ML alerting: test message, ignore if this arrived unexpectedly.")
    except TelegramConfigError as exc:
        raise SystemExit(f"{exc} -- set both env vars before running this") from None
    print("sent -- check the chat for the test message")


if __name__ == "__main__":
    main()
