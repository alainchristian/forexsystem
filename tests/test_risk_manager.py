import sys
from pathlib import Path
import pytest
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src.risk_manager import RiskConfig, RiskManager

def test_calculate_position_size_base():
    """Test base position sizing without Kelly"""
    config = RiskConfig(account_equity=10000.0, risk_per_trade=0.02)
    manager = RiskManager(config)
    
    # 2% of $10,000 = $200 risk.
    # Entry: 1.0500, SL: 1.0480 => 20 pips risk
    # 20 pips * $10 per pip per lot = $200 per lot
    # Size should be 1.0 lot exactly
    size = manager.calculate_position_size(entry_price=1.0500, stop_loss_price=1.0480)
    assert size == 1.0

def test_calculate_position_size_max_constraint():
    """Test that position size never exceeds the hard 5% maximum"""
    config = RiskConfig(account_equity=10000.0, risk_per_trade=0.06)  # Wants 6% risk
    manager = RiskManager(config)
    
    # 2 pips * $10 = $20 per lot.
    # Base formula asks for 6% risk = $600 => 30 lots.
    # BUT max risk is 5% = $500 => max position is $500 / $20 = 25 lots.
    
    size = manager.calculate_position_size(1.0500, 1.0498)
    assert size == 25.0  # Capped at 5%

def test_can_open_trade_circuit_breakers():
    """Test daily loss, drawdown, and global open-trades circuit breakers.

    can_open_trade(symbol, volume, open_positions) takes open_positions as
    a parameter rather than tracking it on the instance, and returns a
    {'valid': bool, 'reason': str} dict rather than a bare bool.
    """
    config = RiskConfig(
        account_equity=10000.0,
        max_daily_loss_pct=0.05,
        max_drawdown_pct=0.15,
        max_open_trades=3
    )
    manager = RiskManager(config)

    assert manager.can_open_trade('EURUSD', 0.1, {})['valid'] is True

    # 1. Test Max Open Trades
    open_positions = {
        1: {'symbol': 'EURUSD', 'volume': 0.1},
        2: {'symbol': 'GBPUSD', 'volume': 0.1},
        3: {'symbol': 'USDJPY', 'volume': 0.1},
    }
    result = manager.can_open_trade('AUDUSD', 0.1, open_positions)
    assert result['valid'] is False
    assert 'Global max open trades' in result['reason']

    # 2. Test Daily Loss Breaker
    manager.update_daily_pnl(-600.0)  # -6% loss today
    assert manager.can_open_trade('EURUSD', 0.1, {})['valid'] is False

    # Reset daily
    manager.reset_daily_stats()
    assert manager.can_open_trade('EURUSD', 0.1, {})['valid'] is True

    # 3. Test Max Drawdown Breaker
    # Peak equity is 10000. We lose 1600. Equity = 8400.
    # Drawdown = (10000 - 8400) / 10000 = 16% > 15%
    # But wait, update_daily_pnl modifies account_equity.
    # Let's reset equity manually to simulate long-term DD without tripping daily limit
    manager.config.account_equity = 8400.0
    manager.daily_pnl = 0.0 # reset daily so it doesn't trip
    assert manager.can_open_trade('EURUSD', 0.1, {})['valid'] is False

def test_validate_trade_setup():
    """Test Reward-to-Risk ratio validation"""
    config = RiskConfig(account_equity=10000.0, min_reward_risk_ratio=1.5)
    manager = RiskManager(config)
    
    # Good setup: Risk 20 pips, Reward 40 pips (2.0 R:R)
    res_good = manager.validate_trade_setup(entry_price=1.0500, stop_loss=1.0480, take_profit=1.0540)
    assert res_good['valid'] is True
    assert res_good['ratio'] == pytest.approx(2.0)
    
    # Bad setup: Risk 20 pips, Reward 20 pips (1.0 R:R)
    res_bad = manager.validate_trade_setup(entry_price=1.0500, stop_loss=1.0480, take_profit=1.0520)
    assert res_bad['valid'] is False
    assert res_bad['ratio'] == pytest.approx(1.0)
