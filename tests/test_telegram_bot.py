import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.telegram_bot import TelegramNotifier

# NOTE: this project deliberately drives async tests with asyncio.run(...)
# rather than @pytest.mark.asyncio - see tests/test_mt5_trader.py for why.

AUTHORIZED_CHAT_ID = "123456"


def _notifier():
    return TelegramNotifier(bot_token="token", chat_id=AUTHORIZED_CHAT_ID)


def _update(chat_id):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    return update


def _context(trader=None):
    context = MagicMock()
    context.bot_data = {'trader': trader if trader is not None else MagicMock()}
    context.args = []
    return context


def test_is_authorized_true_for_configured_chat():
    notifier = _notifier()
    assert notifier._is_authorized(_update(AUTHORIZED_CHAT_ID)) is True
    assert notifier._is_authorized(_update(int(AUTHORIZED_CHAT_ID))) is True  # int vs str chat id


def test_is_authorized_false_for_other_chat():
    notifier = _notifier()
    assert notifier._is_authorized(_update("999999")) is False


def test_cmd_status_rejects_unauthorized():
    notifier = _notifier()
    update = _update("999999")
    context = _context()

    asyncio.run(notifier.cmd_status(update, context))

    update.message.reply_text.assert_not_called()


def test_cmd_positions_rejects_unauthorized():
    notifier = _notifier()
    update = _update("999999")
    trader = MagicMock()
    trader.open_positions = {1: {"symbol": "EURUSD", "volume": 0.1, "entry": 1.1, "sl": 1.09, "tp": 1.12}}
    context = _context(trader)

    asyncio.run(notifier.cmd_positions(update, context))

    update.message.reply_text.assert_not_called()


def test_cmd_close_position_rejects_unauthorized_and_never_closes():
    notifier = _notifier()
    update = _update("999999")
    trader = MagicMock()
    trader.close_position = AsyncMock(return_value=True)
    context = _context(trader)
    context.args = ["555"]

    asyncio.run(notifier.cmd_close_position(update, context))

    trader.close_position.assert_not_called()
    update.message.reply_text.assert_not_called()


def test_cmd_emergency_stop_rejects_unauthorized_and_never_closes():
    notifier = _notifier()
    update = _update("999999")
    trader = MagicMock()
    trader.open_positions = {1: {}, 2: {}}
    trader.close_position = AsyncMock(return_value=True)
    context = _context(trader)

    asyncio.run(notifier.cmd_emergency_stop(update, context))

    trader.close_position.assert_not_called()
    update.message.reply_text.assert_not_called()


def test_cmd_close_position_proceeds_for_authorized_chat():
    """Regression guard: the authorization guard must not block the
    legitimate operator - only reject other chats."""
    notifier = _notifier()
    update = _update(AUTHORIZED_CHAT_ID)
    trader = MagicMock()
    trader.close_position = AsyncMock(return_value=True)
    context = _context(trader)
    context.args = ["555"]

    asyncio.run(notifier.cmd_close_position(update, context))

    trader.close_position.assert_awaited_once_with(555, "Manual close via Telegram")
    update.message.reply_text.assert_called_once()
