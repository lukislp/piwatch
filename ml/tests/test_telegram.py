from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alerting.telegram import (
    AnomalyAlert,
    TelegramConfigError,
    format_anomaly_alert,
    send_anomaly_alert,
    send_message,
)


@patch("alerting.telegram.httpx.post")
def test_send_message_posts_token_in_url_and_chat_id_text_in_body(mock_post):
    mock_post.return_value = MagicMock(raise_for_status=lambda: None)

    send_message("hello", bot_token="tok123", chat_id="42")

    url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
    assert url == "https://api.telegram.org/bottok123/sendMessage"
    assert kwargs["json"] == {"chat_id": "42", "text": "hello"}


@patch("alerting.telegram.httpx.post")
def test_send_message_raises_on_http_error(mock_post):
    response = MagicMock()
    response.raise_for_status.side_effect = RuntimeError("boom")
    mock_post.return_value = response

    with pytest.raises(RuntimeError):
        send_message("hello", bot_token="tok", chat_id="1")


@patch("alerting.telegram.httpx.post")
def test_send_message_falls_back_to_env_vars(mock_post, monkeypatch):
    mock_post.return_value = MagicMock(raise_for_status=lambda: None)
    monkeypatch.setenv("PIWATCH_ML_TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("PIWATCH_ML_TELEGRAM_CHAT_ID", "env-chat")

    send_message("hello")

    url = mock_post.call_args[0][0]
    assert "env-token" in url
    assert mock_post.call_args[1]["json"]["chat_id"] == "env-chat"


def test_send_message_raises_config_error_when_env_vars_missing(monkeypatch):
    monkeypatch.delenv("PIWATCH_ML_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PIWATCH_ML_TELEGRAM_CHAT_ID", raising=False)

    with pytest.raises(TelegramConfigError):
        send_message("hello")


def test_format_anomaly_alert_includes_all_fields():
    alert = AnomalyAlert(node="pinode01", column="cpu_pct", score=7.5, threshold=3.0, timestamp="2026-08-16T12:00:00Z")

    text = format_anomaly_alert(alert)

    assert "pinode01" in text
    assert "cpu_pct" in text
    assert "7.50" in text
    assert "3.00" in text
    assert "2026-08-16T12:00:00Z" in text


@patch("alerting.telegram.httpx.post")
def test_send_anomaly_alert_sends_the_formatted_message(mock_post):
    mock_post.return_value = MagicMock(raise_for_status=lambda: None)
    alert = AnomalyAlert(node="pinode02", column="mem_pct", score=9.1, threshold=3.0, timestamp="t")

    send_anomaly_alert(alert, bot_token="tok", chat_id="1")

    sent_text = mock_post.call_args[1]["json"]["text"]
    assert sent_text == format_anomaly_alert(alert)
