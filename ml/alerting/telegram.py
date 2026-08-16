"""Telegram alerting: sends a message via the Telegram Bot API.

Independent of the rest of the project on purpose -- this module doesn't know
or care what triggered the alert (an anomaly score crossing threshold in the
eventual S5 serving loop, a manual test, anything). It's just "given a
message, deliver it", so it can be built and tested now, well before there's
a trained model or a serving endpoint to plug it into. Telegram specifically
because that's the channel the user's other monitoring already alerts
through -- not Home Assistant, which was the project's original plan.

Setup: create a bot via @BotFather to get a token, then message the bot once
and read https://api.telegram.org/bot<token>/getUpdates to find your chat ID.
Both go in environment variables (PIWATCH_ML_TELEGRAM_BOT_TOKEN,
PIWATCH_ML_TELEGRAM_CHAT_ID) -- never hardcoded or committed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org"

BOT_TOKEN_ENV = "PIWATCH_ML_TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "PIWATCH_ML_TELEGRAM_CHAT_ID"


class TelegramConfigError(RuntimeError):
    """Raised when the bot token/chat ID aren't available from either an
    explicit argument or the environment."""


def _config_from_env() -> tuple[str, str]:
    token = os.environ.get(BOT_TOKEN_ENV)
    chat_id = os.environ.get(CHAT_ID_ENV)
    if not token or not chat_id:
        raise TelegramConfigError(f"{BOT_TOKEN_ENV} and {CHAT_ID_ENV} must both be set")
    return token, chat_id


def send_message(text: str, bot_token: str | None = None, chat_id: str | None = None, timeout: float = 10.0) -> None:
    """Sends `text` via the Telegram Bot API. bot_token/chat_id fall back to
    the PIWATCH_ML_TELEGRAM_BOT_TOKEN/_CHAT_ID env vars when not passed
    explicitly -- passing them explicitly is mainly there for tests, so a
    test never accidentally depends on whatever happens to be in the
    environment it runs in.
    """
    if bot_token is None or chat_id is None:
        env_token, env_chat_id = _config_from_env()
        bot_token = bot_token or env_token
        chat_id = chat_id or env_chat_id

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    response = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=timeout)
    response.raise_for_status()


@dataclass
class AnomalyAlert:
    node: str
    column: str
    score: float
    threshold: float
    timestamp: str


def format_anomaly_alert(alert: AnomalyAlert) -> str:
    """Plain-text formatting kept separate from send_message on purpose: this
    is the one piece that WILL eventually depend on the real scoring pipeline
    (S5 serving), everything above it doesn't -- easy to swap or extend the
    message format later without touching how delivery works.
    """
    return (
        f"⚠️ piwatch anomaly detected\n"
        f"node: {alert.node}\n"
        f"metric: {alert.column}\n"
        f"score: {alert.score:.2f} (threshold {alert.threshold:.2f})\n"
        f"time: {alert.timestamp}"
    )


def send_anomaly_alert(alert: AnomalyAlert, **kwargs) -> None:
    send_message(format_anomaly_alert(alert), **kwargs)
