import logging
import time
from datetime import datetime
from typing import Optional, Dict

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("Warning: MetaTrader5 module not installed or not running on Windows.")

from config.config import TRADE_COOLDOWN_SECONDS, SYMBOLS

class MT5Trader:
    def __init__(self, 
                 account: int,
                 password: str,
                 server: str,
                 risk_manager,
                 telegram_notifier):
        self.account = account
        self.password = password
        self.server = server
        self.risk_mgr = risk_manager
        self.telegram = telegram_notifier
        
        self.mt5_initialized = False
        self.open_positions: Dict = {}
        self.trade_log = []
        self._last_trade_time: Dict[str, float] = {}

        self.logger = logging.getLogger('MT5Trader')
    
    def initialize(self) -> bool:
        """Connect to MT5"""
        if not MT5_AVAILABLE:
            self.logger.error("MetaTrader5 library is not available.")
            return False
            
        try:
            if not mt5.initialize(
                login=int(self.account),
                password=str(self.password),
                server=str(self.server)
            ):
                self.logger.error(f"MT5 init failed: {mt5.last_error()}")
                return False
            
            self.mt5_initialized = True
            self.logger.info(f"Connected to {self.server}, Account: {self.account}")
            return True
        
        except Exception as e:
            self.logger.error(f"Init exception: {e}")
            return False
    
    async def submit_order(self,
                          symbol: str,
                          direction: int,  # 1=BUY, -1=SELL
                          volume: float,
                          entry_price: float,
                          stop_loss: float,
                          take_profit: float,
                          confidence: float = 0.0) -> Optional[int]:
        """Submit market order with SL/TP"""
        if direction not in (1, -1):
            raise ValueError(f"direction must be 1 or -1, got {direction}")
        if any(v <= 0 for v in [volume, entry_price, stop_loss, take_profit]):
            raise ValueError("volume and prices must be positive")

        if not self.mt5_initialized:
            self.logger.error("Cannot submit order: MT5 not initialized")
            return None

        # Per-symbol cooldown — block re-entry within one 4H candle
        now = time.time()
        if symbol in self._last_trade_time:
            elapsed = now - self._last_trade_time[symbol]
            if elapsed < TRADE_COOLDOWN_SECONDS:
                remaining = int(TRADE_COOLDOWN_SECONDS - elapsed)
                self.logger.debug(f"Cooldown active for {symbol}: {remaining}s remaining")
                return None

        # Reject if live spread exceeds the configured maximum for this symbol
        if symbol in SYMBOLS:
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                live_spread = tick.ask - tick.bid
                max_spread = SYMBOLS[symbol].get('max_spread', float('inf'))
                if live_spread > max_spread:
                    self.logger.warning(
                        f"Spread too wide for {symbol}: {live_spread:.5f} > max {max_spread:.5f} — order skipped"
                    )
                    return None

        trade_check = self.risk_mgr.can_open_trade(symbol, float(volume), self.open_positions)
        if not trade_check['valid']:
            # If blocked by max trades limit, try ranked replacement
            if 'max' in trade_check['reason'].lower() and 'trades' in trade_check['reason'].lower() and self.open_positions:
                lowest_conf_id = min(self.open_positions.keys(), key=lambda k: self.open_positions[k].get('confidence', 1.0))
                lowest_conf = self.open_positions[lowest_conf_id].get('confidence', 1.0)
                
                if confidence > lowest_conf:
                    self.logger.info(f"Ranked Replacement: New setup ({confidence:.2%}) > lowest open trade ({lowest_conf:.2%}). Closing #{lowest_conf_id}")
                    if self.close_position(lowest_conf_id, reason="Ranked Replacement"):
                        # Re-check limits after closing
                        trade_check = self.risk_mgr.can_open_trade(symbol, float(volume), self.open_positions)
                        if not trade_check['valid']:
                            self.logger.warning(f"Trade still blocked after replacement: {trade_check['reason']}")
                            return None
                    else:
                        self.logger.error("Failed to close position for replacement.")
                        return None
                else:
                    self.logger.warning(f"Trade blocked: {trade_check['reason']} (New setup {confidence:.2%} <= lowest open {lowest_conf:.2%})")
                    return None
            else:
                self.logger.warning(f"Trade blocked by risk manager: {trade_check['reason']}")
                await self.telegram.send_alert(f"⛔ Trade blocked: {trade_check['reason']}")
                return None
        
        # Validate trade setup
        validation = self.risk_mgr.validate_trade_setup(entry_price, stop_loss, take_profit)
        if not validation['valid']:
            self.logger.warning(f"Invalid setup: {validation['reason']}")
            return None
        
        # Check available margin and cap volume if necessary
        order_type = mt5.ORDER_TYPE_BUY if direction > 0 else mt5.ORDER_TYPE_SELL
        acc_info = mt5.account_info()
        if acc_info:
            margin_req = mt5.order_calc_margin(order_type, symbol, float(volume), float(entry_price))
            if margin_req is not None and margin_req > acc_info.margin_free * 0.8:
                # Calculate safe volume (max 80% of free margin)
                safe_volume = float(volume) * (acc_info.margin_free * 0.8 / margin_req)
                safe_volume = round(max(0.01, safe_volume), 2)
                self.logger.warning(f"Volume {volume} requires {margin_req} margin (Free: {acc_info.margin_free}). Reducing to {safe_volume}")
                volume = safe_volume
                
                # Double check if even minimum volume is too much
                margin_req_new = mt5.order_calc_margin(order_type, symbol, float(volume), float(entry_price))
                if margin_req_new is not None and margin_req_new > acc_info.margin_free:
                    self.logger.error(f"Insufficient funds: {symbol} minimum volume requires {margin_req_new} margin, but only {acc_info.margin_free} is free.")
                    await self.telegram.send_alert(f"⛔ Trade blocked: Insufficient margin for {symbol}")
                    return None
        
        # Prepare order
        order_type = mt5.ORDER_TYPE_BUY if direction > 0 else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "price": float(entry_price),
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "deviation": 5,  # 5 pips slippage tolerance
            "magic": 123456,
            "comment": f"AI-{int(time.time())}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        try:
            result = mt5.order_send(request)
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                self.logger.error(f"Order failed: {result.comment}")
                await self.telegram.send_alert(f"❌ Order failed: {result.comment}")
                return None
            
            # The order was successful
            order_id = result.order
            self.open_positions[order_id] = {
                'symbol': symbol,
                'direction': direction,
                'volume': volume,
                'entry': entry_price,
                'sl': stop_loss,
                'tp': take_profit,
                'opened_at': datetime.now(),
                'order_id': order_id,
                'confidence': confidence
            }
            
            self._last_trade_time[symbol] = time.time()
            self.logger.info(f"✅ Order #{order_id} opened: {symbol} {volume}L @ {entry_price}")
            
            dir_str = "BUY" if direction > 0 else "SELL"
            await self.telegram.send_alert(
                f"✅ <b>{symbol}</b> {dir_str}\n"
                f"Vol: {volume} | Entry: {entry_price:.5f}\n"
                f"SL: {stop_loss:.5f} | TP: {take_profit:.5f}\n"
                f"R:R: {validation['ratio']:.2f}:1"
            )
            return order_id
        
        except Exception as e:
            self.logger.error(f"Order exception: {e}")
            await self.telegram.send_alert(f"⚠️ Order exception: {str(e)[:100]}")
            return None
    
    def close_position(self, order_id: int, reason: str = "Manual") -> bool:
        """Close open position"""
        if not self.mt5_initialized:
            return False
            
        if order_id not in self.open_positions:
            return False
        
        pos = self.open_positions[order_id]
        ticket_info = mt5.positions_get(ticket=order_id)
        
        if not ticket_info or len(ticket_info) == 0:
            return False
        
        ticket = ticket_info[0]
        
        # Get current tick to close at current price
        tick = mt5.symbol_info_tick(pos['symbol'])
        if not tick:
            return False
            
        close_type = mt5.ORDER_TYPE_SELL if pos['direction'] > 0 else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos['symbol'],
            "volume": pos['volume'],
            "type": close_type,
            "position": order_id,
            "price": close_price,
            "deviation": 5,
            "magic": 123456,
            "comment": f"Close {order_id}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            pnl = result.profit  # in account currency
            self.risk_mgr.update_daily_pnl(pnl)
            del self.open_positions[order_id]
            
            self.trade_log.append({
                'order_id': order_id,
                'symbol': pos['symbol'],
                'entry': pos['entry'],
                'exit': result.price,
                'volume': pos['volume'],
                'pnl': pnl,
                'reason': reason,
                'duration': (datetime.now() - pos['opened_at']).total_seconds() / 3600  # hours
            })
            
            self.logger.info(f"❌ Position closed: P&L ${pnl:.2f} ({reason})")
            return True
        
        return False
    
    def get_open_positions(self) -> list:
        """Return all positions currently open on MT5 for this account."""
        if not self.mt5_initialized:
            return []
        positions = mt5.positions_get()
        return list(positions) if positions else []

    def get_account_info(self) -> Dict:
        """Return current account state"""
        if not self.mt5_initialized:
            return {}
            
        acc_info = mt5.account_info()
        if not acc_info:
            return {}
            
        return {
            'balance': acc_info.balance,
            'equity': acc_info.equity,
            'profit': acc_info.profit,
            'margin_free': acc_info.margin_free,
            'margin_level': acc_info.margin_level,
            'open_positions': len(self.open_positions)
        }
    
    def modify_position_sl(self, order_id: int, new_sl: float) -> bool:
        """Move the stop-loss of an open position to new_sl."""
        if not self.mt5_initialized or order_id not in self.open_positions:
            return False
        pos = self.open_positions[order_id]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": order_id,
            "symbol": pos['symbol'],
            "sl": float(new_sl),
            "tp": float(pos['tp']),
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            pos['sl'] = new_sl
            return True
        self.logger.warning(
            f"SL modify failed for #{order_id}: {result.comment if result else 'no result'}"
        )
        return False

    def shutdown(self):
        """Cleanly disconnect"""
        if self.mt5_initialized:
            mt5.shutdown()
            self.logger.info("MT5 disconnected")
