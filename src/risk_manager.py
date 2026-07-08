import numpy as np
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Dict, Optional

from config.config import (
    SYMBOLS, MIN_REPLACEMENT_HOLD_MINUTES, MIN_REPLACEMENT_CONFIDENCE_GAP,
    MIN_REPLACEMENT_PROFIT, RISK_KELLY_MULTIPLIER_MIN, RISK_KELLY_MULTIPLIER_MAX,
    MAX_UNCONFIRMED_PNL_AGE_MINUTES,
)
from src.execution_logic import calculate_kelly_fraction, dollar_per_pip_per_lot

@dataclass
class RiskConfig:
    account_equity: float
    risk_per_trade: float = 0.01  # 1% per trade (capped at max_open_trades x 1% = 10% max exposure)
    max_daily_loss_pct: float = 0.05  # -5% daily stop
    max_drawdown_pct: float = 0.15  # -15% drawdown stop
    max_open_trades: int = 10
    min_reward_risk_ratio: float = 1.5
    max_trades_per_symbol: int = 1   # 1 per symbol — spread across pairs, don't stack
    max_volume_per_symbol: float = 0.5
    min_replacement_hold_minutes: float = MIN_REPLACEMENT_HOLD_MINUTES
    min_replacement_confidence_gap: float = MIN_REPLACEMENT_CONFIDENCE_GAP
    min_replacement_profit: float = MIN_REPLACEMENT_PROFIT
    max_unconfirmed_pnl_age_minutes: float = MAX_UNCONFIRMED_PNL_AGE_MINUTES

class RiskManager:
    def __init__(self, config: RiskConfig,
                 on_state_changed: Optional[Callable[[float, float], None]] = None):
        """on_state_changed(peak_equity, daily_pnl), if given, is invoked whenever
        either value changes (update_daily_pnl, reset_daily_stats) so a caller can
        persist them across restarts (see main.py / src/risk_state.py). Left as an
        injected hook rather than importing persistence directly here so RiskManager
        stays DB-free and every existing bare-construction test keeps working
        unchanged."""
        self.config = config
        self.open_trades = []
        self.daily_pnl = 0.0
        self.peak_equity = config.account_equity
        self.session_start_time = datetime.now()
        self.on_state_changed = on_state_changed
    
    def calculate_position_size(self,
                               entry_price: float,
                               stop_loss_price: float,
                               symbol: Optional[str] = None,
                               historical_trades: Optional[List[Dict]] = None) -> float:
        """
        Calculates position size using Base Risk constraint and Kelly Optimization.
        Constraints ensure it never exceeds a hard 5% maximum absolute risk per trade.
        """
        risk_dollars = self.config.account_equity * self.config.risk_per_trade

        # Use symbol's pip_value so JPY pairs (pip=0.01) are counted correctly
        pip_value = SYMBOLS[symbol]['pip_value'] if symbol and symbol in SYMBOLS else 0.0001
        risk_pips = abs(entry_price - stop_loss_price) / pip_value

        if risk_pips <= 0:
            return 0.0

        dollar_per_pip = dollar_per_pip_per_lot(pip_value, entry_price)

        position_size = risk_dollars / (risk_pips * dollar_per_pip)
        
        # Kelly optimization if we have sufficient trade history
        if historical_trades and len(historical_trades) > 10:
            wins = len([t for t in historical_trades if t['pnl'] > 0])
            losses = len(historical_trades) - wins
            win_rate = wins / len(historical_trades)
            
            if losses > 0:
                avg_win = np.mean([t['pnl'] for t in historical_trades if t['pnl'] > 0])
                avg_loss = abs(np.mean([t['pnl'] for t in historical_trades if t['pnl'] < 0]))
                
                if avg_loss > 0:
                    kelly_frac = calculate_kelly_fraction(
                        win_rate, avg_win, avg_loss,
                        min_frac=RISK_KELLY_MULTIPLIER_MIN, max_frac=RISK_KELLY_MULTIPLIER_MAX,
                    )
                    position_size *= kelly_frac
        
        # Constraint: never risk more than 5% per trade absolute
        max_risk_dollars = self.config.account_equity * 0.05
        max_position = max_risk_dollars / (risk_pips * dollar_per_pip)
        position_size = min(position_size, max_position)

        # Clamp to symbol-specific max lot size
        if symbol and symbol in SYMBOLS:
            symbol_max = SYMBOLS[symbol].get('max_lot', 100.0)
            position_size = min(position_size, symbol_max)

        return round(max(0.01, position_size), 2)
    
    def can_open_trade(self, symbol: str, volume: float, open_positions: dict,
                       pending_pnl: Optional[dict] = None) -> dict:
        """Check if we can open a new trade based on circuit breakers and limits"""
        # Unconfirmed P&L breaker: daily_pnl/peak_equity below are unreliable
        # while a close is still unreconciled (see mt5_trader.queue_pnl_reconciliation).
        # Hard-blocks new trades once any entry has been stuck longer than
        # max_unconfirmed_pnl_age_minutes, on top of (not instead of) the
        # existing Telegram alert escalation in reconcile_pending_pnl.
        if pending_pnl:
            oldest_age_minutes = max(
                (datetime.now() - entry['queued_at']).total_seconds() / 60
                for entry in pending_pnl.values()
            )
            if oldest_age_minutes >= self.config.max_unconfirmed_pnl_age_minutes:
                return {'valid': False, 'reason': 'Unconfirmed P&L pending reconciliation'}

        # Daily loss limit breaker
        if self.daily_pnl < -self.config.account_equity * self.config.max_daily_loss_pct:
            return {'valid': False, 'reason': 'Daily loss limit reached'}
        
        # Drawdown limit breaker
        drawdown = (self.peak_equity - self.config.account_equity) / self.peak_equity
        if drawdown > self.config.max_drawdown_pct:
            return {'valid': False, 'reason': 'Max drawdown reached'}
        
        # Max global open trades limit
        if len(open_positions) >= self.config.max_open_trades:
            return {'valid': False, 'reason': 'Global max open trades reached'}
            
        # Symbol specific limits
        symbol_trades = [pos for pos in open_positions.values() if pos['symbol'] == symbol]
        if len(symbol_trades) >= self.config.max_trades_per_symbol:
            return {'valid': False, 'reason': f'Max trades for {symbol} ({self.config.max_trades_per_symbol}) reached'}
            
        symbol_volume = sum(pos['volume'] for pos in symbol_trades)
        if symbol_volume + volume > self.config.max_volume_per_symbol:
            return {'valid': False, 'reason': f'Max volume for {symbol} ({self.config.max_volume_per_symbol}) exceeded'}
        
        return {'valid': True, 'reason': 'OK'}
    
    def validate_trade_setup(self,
                            entry_price: float,
                            stop_loss: float,
                            take_profit: float,
                            symbol: Optional[str] = None) -> dict:
        """Check R:R ratio and other trade quality metrics"""
        # Use symbol's pip_value so JPY pairs (pip=0.01) are counted correctly
        pip_value = SYMBOLS[symbol]['pip_value'] if symbol and symbol in SYMBOLS else 0.0001
        risk_pips = abs(entry_price - stop_loss) / pip_value
        reward_pips = abs(take_profit - entry_price) / pip_value
        
        ratio = round(reward_pips / (risk_pips + 1e-6), 2)
        
        return {
            'valid': ratio >= self.config.min_reward_risk_ratio,
            'ratio': ratio,
            'risk_pips': risk_pips,
            'reward_pips': reward_pips,
            'reason': f"R:R {ratio:.2f}:1" if ratio >= self.config.min_reward_risk_ratio 
                     else f"Poor R:R {ratio:.2f}:1 (min {self.config.min_reward_risk_ratio}:1)"
        }
    
    def update_daily_pnl(self, closed_trade_pnl: float):
        """Track daily P&L and adjust account equity"""
        self.daily_pnl += closed_trade_pnl
        self.config.account_equity += closed_trade_pnl
        self.peak_equity = max(self.peak_equity, self.config.account_equity)
        if self.on_state_changed:
            self.on_state_changed(self.peak_equity, self.daily_pnl)

    def reset_daily_stats(self):
        """Reset at session end (e.g., 5PM ET)"""
        self.daily_pnl = 0.0
        self.session_start_time = datetime.now()
        if self.on_state_changed:
            self.on_state_changed(self.peak_equity, self.daily_pnl)
