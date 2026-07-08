import sys
from pathlib import Path
from datetime import datetime, timedelta
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

def test_calculate_position_size_applies_kelly_multiplier_when_history_given():
    """historical_trades isn't wired in from main.py yet (a separate,
    deliberate follow-up), so this is the first real exercise of the Kelly
    branch - confirms it actually multiplies the base size rather than
    being dead code that happens to never raise."""
    config = RiskConfig(account_equity=10000.0, risk_per_trade=0.01)
    manager = RiskManager(config)

    # Entry/SL -> 20 pips risk, base size = ($100 risk) / (20 pips * $10/pip) = 0.5 lots
    base_size = manager.calculate_position_size(entry_price=1.0500, stop_loss_price=1.0480)
    assert base_size == 0.5

    # 12 trades, 8 wins @ +$50, 4 losses @ -$30 -> win_rate=2/3, avg_win=50, avg_loss=30
    # kelly = (2/3*50 - 1/3*30) / 50 = (33.33 - 10) / 50 = 0.4667 -> clamped to [0.5, 1.5] => 0.5
    historical_trades = [{"pnl": 50.0}] * 8 + [{"pnl": -30.0}] * 4
    kelly_adjusted = manager.calculate_position_size(
        entry_price=1.0500, stop_loss_price=1.0480, historical_trades=historical_trades
    )

    assert kelly_adjusted == round(base_size * 0.5, 2)


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

def test_update_daily_pnl_invokes_on_state_changed_hook_with_current_values():
    """RiskManager persists peak_equity/daily_pnl via an injected hook rather
    than importing a DB module directly (keeps this class DB-free and every
    bare-construction test in this file working with zero mocking)."""
    calls = []
    config = RiskConfig(account_equity=10000.0)
    manager = RiskManager(config, on_state_changed=lambda pe, dp: calls.append((pe, dp)))

    manager.update_daily_pnl(500.0)

    assert calls == [(10500.0, 500.0)]


def test_reset_daily_stats_invokes_on_state_changed_hook():
    """reset_daily_stats() must also fire the hook so a persisted daily_pnl_date
    tracks the reset - otherwise a later restart on the same day could
    incorrectly restore the pre-reset value."""
    calls = []
    config = RiskConfig(account_equity=10000.0)
    manager = RiskManager(config, on_state_changed=lambda pe, dp: calls.append((pe, dp)))

    manager.update_daily_pnl(-200.0)
    manager.reset_daily_stats()

    assert calls[-1] == (10000.0, 0.0)


def test_on_state_changed_defaults_to_none_and_is_never_required():
    """Omitting on_state_changed (today's default, used by every other test in
    this file) must not raise or otherwise change behavior."""
    config = RiskConfig(account_equity=10000.0)
    manager = RiskManager(config)

    manager.update_daily_pnl(100.0)
    manager.reset_daily_stats()  # must not raise


def test_can_open_trade_blocks_when_pending_pnl_stale():
    """A pending_pnl entry older than max_unconfirmed_pnl_age_minutes must hard
    -block new trades, even though daily_pnl/peak_equity look fine - those
    figures are unreliable while a close is still unreconciled (see
    mt5_trader.queue_pnl_reconciliation / reconcile_pending_pnl)."""
    config = RiskConfig(account_equity=10000.0, max_unconfirmed_pnl_age_minutes=15.0)
    manager = RiskManager(config)

    pending_pnl = {
        123: {"symbol": "EURUSD", "reason": "SL hit", "queued_at": datetime.now() - timedelta(minutes=20), "attempts": 4}
    }

    result = manager.can_open_trade('GBPUSD', 0.1, {}, pending_pnl)
    assert result['valid'] is False
    assert result['reason'] == 'Unconfirmed P&L pending reconciliation'


def test_can_open_trade_allows_when_pending_pnl_within_age_cutoff():
    """A pending_pnl entry younger than the threshold must NOT block trading -
    this is a hard age cutoff (trading resumes once the entry ages back below
    threshold or reconciles), not a permanent block until reconciled."""
    config = RiskConfig(account_equity=10000.0, max_unconfirmed_pnl_age_minutes=15.0)
    manager = RiskManager(config)

    pending_pnl = {
        123: {"symbol": "EURUSD", "reason": "SL hit", "queued_at": datetime.now() - timedelta(minutes=5), "attempts": 1}
    }

    result = manager.can_open_trade('GBPUSD', 0.1, {}, pending_pnl)
    assert result['valid'] is True


def test_can_open_trade_ignores_empty_pending_pnl():
    """Existing circuit-breaker behavior must be unchanged when pending_pnl is
    omitted or empty - proves the new parameter is backward compatible."""
    config = RiskConfig(account_equity=10000.0)
    manager = RiskManager(config)

    assert manager.can_open_trade('EURUSD', 0.1, {})['valid'] is True
    assert manager.can_open_trade('EURUSD', 0.1, {}, {})['valid'] is True
    assert manager.can_open_trade('EURUSD', 0.1, {}, None)['valid'] is True


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

def test_validate_trade_setup_uses_symbol_pip_value_for_jpy_pairs():
    """USDJPY's pip_value is 0.01, not the 0.0001 default - risk_pips/reward_pips
    must be computed with the symbol's real pip size, not a hardcoded *10000,
    or these raw fields are silently off by ~100x for JPY pairs (the ratio
    happened to cancel the old constant out, but the raw pip counts didn't)."""
    config = RiskConfig(account_equity=10000.0, min_reward_risk_ratio=1.5)
    manager = RiskManager(config)

    # Risk 0.20 price units = 20 pips at pip_value=0.01; reward 0.40 = 40 pips
    res = manager.validate_trade_setup(
        entry_price=150.00, stop_loss=149.80, take_profit=150.40, symbol='USDJPY'
    )
    assert res['risk_pips'] == pytest.approx(20.0)
    assert res['reward_pips'] == pytest.approx(40.0)
    assert res['ratio'] == pytest.approx(2.0)
    assert res['valid'] is True

def test_validate_trade_setup_falls_back_to_default_pip_value_without_symbol():
    """No symbol (or an unknown one) must fall back to the same 0.0001 default
    calculate_position_size uses, so existing callers that don't pass a symbol
    keep getting today's non-JPY behavior."""
    config = RiskConfig(account_equity=10000.0, min_reward_risk_ratio=1.5)
    manager = RiskManager(config)

    res_no_symbol = manager.validate_trade_setup(entry_price=1.0500, stop_loss=1.0480, take_profit=1.0540)
    assert res_no_symbol['risk_pips'] == pytest.approx(20.0)
    assert res_no_symbol['reward_pips'] == pytest.approx(40.0)

    res_unknown_symbol = manager.validate_trade_setup(
        entry_price=1.0500, stop_loss=1.0480, take_profit=1.0540, symbol='NOTASYMBOL'
    )
    assert res_unknown_symbol['risk_pips'] == pytest.approx(20.0)
    assert res_unknown_symbol['reward_pips'] == pytest.approx(40.0)
