import sys
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.mt5_trader import MT5Trader
from src.risk_manager import RiskManager, RiskConfig

@pytest.fixture
def mock_dependencies():
    risk_config = RiskConfig(account_equity=10000.0)
    risk_mgr = RiskManager(risk_config)
    risk_mgr.can_open_trade = MagicMock(return_value=True)
    risk_mgr.validate_trade_setup = MagicMock(return_value={'valid': True, 'ratio': 2.0})
    
    telegram = AsyncMock()
    
    return risk_mgr, telegram

@pytest.mark.asyncio
@patch('src.mt5_trader.mt5')
async def test_submit_order_success(mock_mt5, mock_dependencies):
    """Test successful order submission and verify request dictionary format"""
    risk_mgr, telegram = mock_dependencies
    
    # Setup mocks
    mock_mt5.initialize.return_value = True
    mock_mt5.ORDER_TYPE_BUY = 0
    mock_mt5.ORDER_TYPE_SELL = 1
    mock_mt5.TRADE_ACTION_DEAL = 1
    mock_mt5.TRADE_RETCODE_DONE = 10009
    
    mock_result = MagicMock()
    mock_result.retcode = 10009
    mock_result.order = 12345
    mock_mt5.order_send.return_value = mock_result
    
    # We must patch MT5_AVAILABLE globally in mt5_trader
    with patch('src.mt5_trader.MT5_AVAILABLE', True):
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader.initialize()
        
        order_id = await trader.submit_order(
            symbol="EURUSD",
            direction=1,  # BUY
            volume=1.5,
            entry_price=1.1000,
            stop_loss=1.0980,
            take_profit=1.1040
        )
        
        assert order_id == 12345
        assert 12345 in trader.open_positions
        
        # Verify the MT5 request dictionary was built correctly
        request_arg = mock_mt5.order_send.call_args[0][0]
        assert request_arg['symbol'] == "EURUSD"
        assert request_arg['volume'] == 1.5
        assert request_arg['type'] == mock_mt5.ORDER_TYPE_BUY
        assert request_arg['sl'] == 1.0980
        assert request_arg['tp'] == 1.1040
        assert request_arg['price'] == 1.1000
        
        # Verify telegram was notified
        telegram.send_alert.assert_called_once()

@pytest.mark.asyncio
@patch('src.mt5_trader.mt5')
async def test_submit_order_blocked_by_risk(mock_mt5, mock_dependencies):
    """Test that Risk Manager can block trades before sending to MT5"""
    risk_mgr, telegram = mock_dependencies
    risk_mgr.can_open_trade.return_value = False  # Block trade!
    
    with patch('src.mt5_trader.MT5_AVAILABLE', True):
        trader = MT5Trader(123, 'pass', 'server', risk_mgr, telegram)
        trader.initialize()
        
        order_id = await trader.submit_order("EURUSD", 1, 1.0, 1.1, 1.0, 1.2)
        
        assert order_id is None
        mock_mt5.order_send.assert_not_called()  # It never hit the API
        telegram.send_alert.assert_called_once_with("⛔ Trade blocked: Risk limits exceeded")
