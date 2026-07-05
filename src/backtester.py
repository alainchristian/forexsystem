"""
Enhanced Backtester Module - Phase 1
Walk-forward validation, realistic slippage, and Kelly Criterion position sizing
"""

import numpy as np
import pandas as pd
import logging
from typing import List, Tuple, Dict, Callable, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import json

from config.config import (
    BACKTEST, LOGS_DIR, BACKTEST_KELLY_FRACTION_MIN, BACKTEST_KELLY_FRACTION_MAX,
)
from src.execution_logic import calculate_kelly_fraction

logger = logging.getLogger(__name__)

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class Trade:
    """Represents a single trade"""
    entry_idx: int
    entry_time: datetime
    entry_price: float
    exit_idx: int
    exit_time: datetime
    exit_price: float
    direction: str  # 'LONG' or 'SHORT'
    volume: float
    slippage_pips: float
    commission: float
    
    @property
    def risk_pips(self) -> float:
        """Risk in pips"""
        return abs(self.entry_price - self.exit_price) * 10000
    
    @property
    def pnl_before_costs(self) -> float:
        """P&L before slippage and commission"""
        if self.direction == 'LONG':
            return (self.exit_price - self.entry_price) * self.volume * 100000
        else:
            return (self.entry_price - self.exit_price) * self.volume * 100000
    
    @property
    def pnl(self) -> float:
        """P&L after slippage and commission"""
        # Slippage acts against the trade (1 pip = 0.0001, so 100000 units * 0.0001 = $10 per pip per lot)
        slippage_cost = self.slippage_pips * 10 * self.volume
        
        if self.direction == 'LONG':
            return ((self.exit_price - self.entry_price) * self.volume * 100000
                   - slippage_cost - self.commission)
        else:
            return ((self.entry_price - self.exit_price) * self.volume * 100000
                   - slippage_cost - self.commission)
    
    @property
    def pnl_pct(self) -> float:
        """P&L as percentage of entry price"""
        return self.pnl / (self.entry_price * self.volume + 1e-10)
    
    @property
    def duration_hours(self) -> float:
        """Trade duration in hours"""
        return (self.exit_time - self.entry_time).total_seconds() / 3600
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        d = asdict(self)
        d['pnl'] = self.pnl
        d['pnl_pct'] = self.pnl_pct
        d['risk_pips'] = self.risk_pips
        d['duration_hours'] = self.duration_hours
        return d


# ============================================================================
# BACKTESTER CLASS
# ============================================================================

class Backtester:
    """
    Enhanced backtester with realistic market conditions
    
    Features:
    - Walk-forward validation
    - Realistic slippage and commissions
    - Kelly Criterion position sizing
    - Comprehensive performance metrics
    """
    
    def __init__(self,
                 df: pd.DataFrame,
                 initial_capital: float = BACKTEST['initial_capital'],
                 risk_per_trade: float = BACKTEST['initial_margin'],
                 slippage_pips: float = BACKTEST['slippage_pips'],
                 commission: float = BACKTEST['commission_per_trade']):
        """
        Initialize backtester
        
        Args:
            df: DataFrame with timestamp index and OHLCV columns
            initial_capital: Starting capital in USD
            risk_per_trade: Risk fraction per trade (0.02 = 2%)
            slippage_pips: Fixed slippage in pips
            commission: Fixed commission per trade in USD
        """
        
        if df is None or df.empty:
            raise ValueError("DataFrame cannot be empty")
        
        self.df = df.reset_index(drop=True)
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.slippage_pips = slippage_pips
        self.commission = commission
        
        # Trading state
        self.trades: List[Trade] = []
        self.equity_curve = [initial_capital]
        self.position = None  # Current open position dict
        
        # Metrics
        self.daily_pnl = {}  # Track P&L by day
        
        logger.info(f"Backtester initialized: ${initial_capital}, "
                   f"{risk_per_trade*100:.1f}% risk, "
                   f"{slippage_pips}pips slippage")
    
    def run_walk_forward(self,
                        signal_func: Callable,
                        train_period: int = BACKTEST['walk_forward']['train_period'],
                        test_period: int = BACKTEST['walk_forward']['test_period'],
                        features_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        Run walk-forward validation
        
        Args:
            signal_func: Function(df, features_df) -> np.array of signals (1/-1/0)
            train_period: Training period in trading days
            test_period: Testing period in trading days
            features_df: Optional pre-computed features
        
        Returns:
            DataFrame with walk-forward results
        """
        
        results = []
        
        for i in range(train_period, len(self.df) - test_period, test_period):
            train_df = self.df.iloc[i-train_period:i].reset_index(drop=True)
            test_df = self.df.iloc[i:i+test_period].reset_index(drop=True)
            
            train_features = features_df.iloc[i-train_period:i].reset_index(drop=True) if features_df is not None else None
            test_features = features_df.iloc[i:i+test_period].reset_index(drop=True) if features_df is not None else None
            
            # Generate signals for test period
            try:
                signals = signal_func(test_df, test_features)
            except Exception as e:
                logger.error(f"Signal generation failed: {e}")
                signals = np.zeros(len(test_df))
            
            # Backtest on test period
            capital_before = self.capital
            self.backtest(test_df, signals)
            
            # Record results
            trades_in_period = len([t for t in self.trades if i <= t.entry_idx < i + test_period])
            
            results.append({
                'period': i // test_period,
                'start_idx': i,
                'end_idx': i + test_period,
                'trades': trades_in_period,
                'capital_start': capital_before,
                'capital_end': self.capital,
                'pnl': self.capital - capital_before,
                'pnl_pct': (self.capital - capital_before) / capital_before * 100,
                'sharpe': self._calculate_sharpe(i, i+test_period),
                'max_dd': self._calculate_max_drawdown(i, i+test_period),
                'win_rate': self._calculate_win_rate(i, i+test_period),
                'profit_factor': self._calculate_profit_factor(i, i+test_period)
            })
            
            logger.info(f"Period {results[-1]['period']}: "
                       f"Trades={trades_in_period}, "
                       f"P&L=${results[-1]['pnl']:.2f} ({results[-1]['pnl_pct']:.2f}%), "
                       f"Sharpe={results[-1]['sharpe']:.2f}")
        
        return pd.DataFrame(results)
    
    def backtest(self, df: pd.DataFrame, signals: np.ndarray) -> None:
        """
        Execute backtest with given signals
        
        Args:
            df: OHLCV DataFrame (reset index)
            signals: Array of signals (1=BUY, -1=SELL, 0=HOLD)
        """
        
        if len(signals) != len(df):
            raise ValueError(f"Signal length ({len(signals)}) != DataFrame length ({len(df)})")
        
        for idx in range(len(df)):
            signal = signals[idx]
            row = df.iloc[idx]
            
            # Close position on SELL signal if long, or BUY signal if short
            if self.position is not None:
                should_close = ((self.position['direction'] == 'LONG' and signal == -1) or
                               (self.position['direction'] == 'SHORT' and signal == 1) or
                               (signal == 0 and idx == len(df) - 1))  # Close at end
                
                if should_close:
                    self._close_position(idx, row)
            
            # Open new position on signal
            if signal != 0 and self.position is None:
                direction = 'LONG' if signal > 0 else 'SHORT'
                volume = self._calculate_position_size(row['close'])
                
                if volume > 0:
                    self.position = {
                        'direction': direction,
                        'entry_idx': idx,
                        'entry_time': row['timestamp'] if 'timestamp' in row else idx,
                        'entry_price': row['close'],
                        'volume': volume
                    }
        
        # Close remaining position at end
        if self.position is not None and len(df) > 0:
            last_row = df.iloc[-1]
            self._close_position(len(df) - 1, last_row)
    
    def _close_position(self, idx: int, row: pd.Series) -> None:
        """Close current position"""
        if self.position is None:
            return
        
        # Add slippage to exit price
        slippage_adjustment = (self.slippage_pips / 10000)
        if self.position['direction'] == 'LONG':
            exit_price = row['close'] - slippage_adjustment
        else:
            exit_price = row['close'] + slippage_adjustment
        
        trade = Trade(
            entry_idx=self.position['entry_idx'],
            entry_time=self.position['entry_time'],
            entry_price=self.position['entry_price'],
            exit_idx=idx,
            exit_time=row['timestamp'] if 'timestamp' in row else idx,
            exit_price=exit_price,
            direction=self.position['direction'],
            volume=self.position['volume'],
            slippage_pips=self.slippage_pips,
            commission=self.commission
        )
        
        self.trades.append(trade)
        self.capital += trade.pnl
        self.equity_curve.append(self.capital)
        
        # Track daily P&L
        day_key = str(trade.exit_time)[:10] if isinstance(trade.exit_time, (pd.Timestamp, datetime)) else 'unknown'
        self.daily_pnl[day_key] = self.daily_pnl.get(day_key, 0) + trade.pnl
        
        self.position = None
    
    def _calculate_position_size(self, entry_price: float) -> float:
        """Calculate position size using Kelly Criterion"""
        
        if not self.trades or len(self.trades) < 10:
            # Use fixed risk fraction until we have enough trades
            risk_dollars = self.capital * self.risk_per_trade
            return risk_dollars / entry_price / 100  # Convert to lots (1 lot = 100k)
        
        # Calculate Kelly fraction from trade history
        wins = len([t for t in self.trades if t.pnl > 0])
        losses = len(self.trades) - wins

        if losses == 0:
            # No losing trades yet - can't compute an avg_loss, so max out
            # the allowed fraction rather than divide by an unknown.
            kelly_fraction = BACKTEST_KELLY_FRACTION_MAX
        else:
            win_rate = wins / len(self.trades)
            avg_win = np.mean([t.pnl for t in self.trades if t.pnl > 0]) if wins > 0 else 0
            avg_loss = abs(np.mean([t.pnl for t in self.trades if t.pnl < 0]))
            kelly_fraction = calculate_kelly_fraction(
                win_rate, avg_win, avg_loss,
                min_frac=BACKTEST_KELLY_FRACTION_MIN, max_frac=BACKTEST_KELLY_FRACTION_MAX,
            )

        risk_dollars = self.capital * kelly_fraction
        # Convert USD to lots (assuming standard 100k lot size)
        position_size = risk_dollars / entry_price / 100
        
        return max(0.01, min(100.0, position_size))
    
    def _calculate_sharpe(self, start_idx: int = 0, end_idx: Optional[int] = None) -> float:
        """Calculate Sharpe ratio for period"""
        if end_idx is None:
            end_idx = len(self.equity_curve)
        
        eq_slice = self.equity_curve[start_idx:end_idx]
        if len(eq_slice) < 2:
            return 0.0
        
        returns = np.diff(eq_slice) / eq_slice[:-1]
        if len(returns) == 0:
            return 0.0
        
        annual_return = np.mean(returns) * 252
        annual_std = np.std(returns) * np.sqrt(252)
        risk_free_rate = BACKTEST.get('risk_free_rate', 0.02)
        
        sharpe = (annual_return - risk_free_rate) / (annual_std + 1e-10)
        return sharpe
    
    def _calculate_max_drawdown(self, start_idx: int = 0, end_idx: Optional[int] = None) -> float:
        """Calculate maximum drawdown for period"""
        if end_idx is None:
            end_idx = len(self.equity_curve)
        
        eq_slice = np.array(self.equity_curve[start_idx:end_idx])
        if len(eq_slice) < 2:
            return 0.0
        
        cum_max = np.maximum.accumulate(eq_slice)
        drawdown = (eq_slice - cum_max) / cum_max
        
        return abs(drawdown.min())
    
    def _calculate_win_rate(self, start_idx: int = 0, end_idx: Optional[int] = None) -> float:
        """Calculate win rate for period"""
        trades_in_period = [t for t in self.trades 
                           if start_idx <= t.entry_idx < (end_idx or len(self.equity_curve))]
        
        if not trades_in_period:
            return 0.0
        
        wins = len([t for t in trades_in_period if t.pnl > 0])
        return wins / len(trades_in_period)
    
    def _calculate_profit_factor(self, start_idx: int = 0, end_idx: Optional[int] = None) -> float:
        """Calculate profit factor for period"""
        trades_in_period = [t for t in self.trades 
                           if start_idx <= t.entry_idx < (end_idx or len(self.equity_curve))]
        
        if not trades_in_period:
            return 0.0
        
        gross_profit = sum([t.pnl for t in trades_in_period if t.pnl > 0])
        gross_loss = abs(sum([t.pnl for t in trades_in_period if t.pnl < 0]))
        
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0
        
        return gross_profit / gross_loss
    
    def report(self) -> Dict:
        """Generate comprehensive backtest report"""
        
        total_trades = len(self.trades)
        winning_trades = len([t for t in self.trades if t.pnl > 0])
        losing_trades = total_trades - winning_trades
        
        if total_trades == 0:
            logger.warning("No trades executed during backtest")
            return {
                'status': 'No trades',
                'total_trades': 0
            }
        
        gross_profit = sum([t.pnl for t in self.trades if t.pnl > 0])
        gross_loss = abs(sum([t.pnl for t in self.trades if t.pnl < 0]))
        
        avg_win = gross_profit / winning_trades if winning_trades > 0 else 0
        avg_loss = gross_loss / losing_trades if losing_trades > 0 else 0
        
        total_pnl = self.equity_curve[-1] - self.initial_capital
        
        report = {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': winning_trades / total_trades,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': gross_profit / (gross_loss + 1e-10),
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
            'total_pnl': total_pnl,
            'total_pnl_pct': (total_pnl / self.initial_capital) * 100,
            'sharpe_ratio': self._calculate_sharpe(),
            'max_drawdown': self._calculate_max_drawdown(),
            'recovery_factor': total_pnl / (self.initial_capital * self._calculate_max_drawdown() + 1e-10),
            'avg_trade_duration_hours': np.mean([t.duration_hours for t in self.trades]),
            'consecutive_wins': self._calc_consecutive_wins(),
            'consecutive_losses': self._calc_consecutive_losses(),
        }
        
        return report
    
    def _calc_consecutive_wins(self) -> int:
        """Maximum consecutive winning trades"""
        max_streak = 0
        current_streak = 0
        
        for trade in self.trades:
            if trade.pnl > 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        
        return max_streak
    
    def _calc_consecutive_losses(self) -> int:
        """Maximum consecutive losing trades"""
        max_streak = 0
        current_streak = 0
        
        for trade in self.trades:
            if trade.pnl < 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        
        return max_streak
    
    def export_trades(self, filepath: str) -> None:
        """Export trades to CSV"""
        trades_data = [t.to_dict() for t in self.trades]
        df_trades = pd.DataFrame(trades_data)
        df_trades.to_csv(filepath, index=False)
        logger.info(f"Exported {len(trades_data)} trades to {filepath}")
    
    def export_report(self, filepath: str) -> None:
        """Export report to JSON"""
        report = self.report()
        with open(filepath, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"Exported report to {filepath}")

    def export_equity_curve(self, filepath: str) -> None:
        """Export equity curve to CSV (trade_num, equity)."""
        df = pd.DataFrame({
            'trade_num': range(len(self.equity_curve)),
            'equity': self.equity_curve,
        })
        df.to_csv(filepath, index=False)
        logger.info(f"Equity curve exported to {filepath}")

    def export_drawdown_curve(self, filepath: str) -> None:
        """Export drawdown curve to CSV (trade_num, equity, drawdown_pct)."""
        equity = np.array(self.equity_curve)
        cum_max = np.maximum.accumulate(equity)
        drawdown_pct = (equity - cum_max) / (cum_max + 1e-10) * 100
        df = pd.DataFrame({
            'trade_num': range(len(equity)),
            'equity': equity,
            'drawdown_pct': drawdown_pct,
        })
        df.to_csv(filepath, index=False)
        logger.info(f"Drawdown curve exported to {filepath}")


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

def run_backtest(df: pd.DataFrame,
                signal_func: Callable,
                features_df: Optional[pd.DataFrame] = None,
                **kwargs) -> Backtester:
    """
    Run single backtest with given signals
    
    Args:
        df: OHLCV DataFrame
        signal_func: Function returning signal array
        features_df: Optional feature DataFrame
        **kwargs: Additional backtester arguments
    
    Returns:
        Backtester object with results
    """
    
    bt = Backtester(df, **kwargs)
    signals = signal_func(df, features_df)
    bt.backtest(df.reset_index(drop=True), signals)
    
    return bt


if __name__ == '__main__':
    print("Enhanced Backtester - Testing")
    print("=" * 60)
    
    # Create dummy data
    dates = pd.date_range(start='2023-01-01', periods=252, freq='D')
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(252) * 0.5)
    
    df = pd.DataFrame({
        'timestamp': dates,
        'open': close + np.random.randn(252) * 0.2,
        'high': close + abs(np.random.randn(252) * 0.3),
        'low': close - abs(np.random.randn(252) * 0.3),
        'close': close,
        'volume': np.random.randint(1000000, 5000000, 252)
    })
    
    # Simple signal function (RSI-based)
    def rsi_signal(df, features):
        returns = df['close'].diff()
        rs = returns.rolling(14).mean() / abs(returns.rolling(14).mean()) + 1
        rsi = 100 - (100 / (1 + rs))
        
        signals = np.zeros(len(df))
        signals[rsi < 30] = 1  # Oversold, buy
        signals[rsi > 70] = -1  # Overbought, sell
        
        return signals
    
    # Run backtest
    bt = Backtester(df, initial_capital=10000)
    signals = rsi_signal(df, None)
    bt.backtest(df.reset_index(drop=True), signals)
    
    report = bt.report()
    print("\n✅ Backtest Results:")
    for key, value in report.items():
        if isinstance(value, float):
            print(f"  {key:.<30} {value:.4f}")
        else:
            print(f"  {key:.<30} {value}")
