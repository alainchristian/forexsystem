import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("Warning: MetaTrader5 module not installed.")

from config.config import TRADE_COOLDOWN_SECONDS, SYMBOLS, PNL_RECONCILE_ALERT_INTERVAL

BRIDGE_DIR = Path(
    r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files\forex_bridge"
)


class _Position:
    """Lightweight stand-in for mt5.TradePosition used in bridge mode."""
    def __init__(self, d: dict):
        self.ticket   = int(d.get("ticket", 0))
        self.symbol   = str(d.get("symbol", ""))
        self.type     = int(d.get("type", 0))   # 0=BUY, 1=SELL
        self.volume   = float(d.get("volume", 0))
        self.price_open = float(d.get("price_open", 0))
        self.sl       = float(d.get("sl", 0))
        self.tp       = float(d.get("tp", 0))
        self.profit   = float(d.get("profit", 0))
        self.time     = int(d.get("time", 0))
        self.magic    = 123456


class MT5Trader:
    def __init__(self, account, password, server,
                 risk_manager, telegram_notifier):
        self.account  = account
        self.password = password
        self.server   = server
        self.risk_mgr = risk_manager
        self.telegram = telegram_notifier

        self.mt5_initialized = False
        self._bridge_mode    = False
        self.open_positions: Dict = {}
        self.trade_log = []
        self._last_trade_time: Dict[str, float] = {}
        self.pending_pnl: Dict[int, dict] = {}
        self.logger = logging.getLogger("MT5Trader")

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Try IPC first; fall back to file-bridge if IPC is unavailable."""
        if MT5_AVAILABLE:
            try:
                if mt5.initialize(
                    login=int(self.account),
                    password=str(self.password),
                    server=str(self.server),
                    timeout=8000
                ):
                    # Wait for terminal to finish syncing account data (Netting VPS quirk)
                    acc = None
                    for attempt in range(10):
                        acc = mt5.account_info()
                        if acc is not None:
                            break
                        self.logger.info(f"Waiting for account sync... attempt {attempt+1}/10")
                        time.sleep(2)
                    if acc is None:
                        self.logger.error("MT5 initialized but account_info() never returned data — disconnecting")
                        mt5.shutdown()
                    else:
                        self.mt5_initialized = True
                        self._bridge_mode    = False
                        self.logger.info(f"MT5 IPC connected — {self.server} #{self.account} | Balance: {acc.balance:.2f} {acc.currency}")
                        return True
                self.logger.warning(f"MT5 IPC failed: {mt5.last_error()} — trying bridge mode")
            except Exception as e:
                self.logger.warning(f"MT5 IPC exception: {e} — trying bridge mode")

        # Bridge mode: verify the bridge directory and EA account file exist
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        account_file = BRIDGE_DIR / "account.json"
        deadline = time.time() + 15
        while time.time() < deadline:
            if account_file.exists() and account_file.stat().st_size > 5:
                break
            time.sleep(1)

        if account_file.exists():
            self.mt5_initialized = True
            self._bridge_mode    = True
            self.logger.info("MT5 bridge mode active — SignalBridge EA detected")
            return True

        self.logger.error(
            "MT5 bridge not ready. Attach SignalBridge.mq5 EA to a chart in MT5 "
            "and wait a few seconds, then restart."
        )
        return False

    def shutdown(self):
        if self.mt5_initialized and not self._bridge_mode and MT5_AVAILABLE:
            mt5.shutdown()
        self.logger.info("MT5Trader shutdown")

    # ------------------------------------------------------------------
    # Bridge helpers
    # ------------------------------------------------------------------

    def _bridge_write(self, payload: dict):
        """Write a signal JSON to the bridge directory."""
        (BRIDGE_DIR / "signal.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    async def _bridge_wait_result(self, signal_id: str, timeout: int = 30) -> Optional[dict]:
        """Wait for result.json to contain a response for signal_id."""
        result_file = BRIDGE_DIR / "result.json"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if result_file.exists():
                    data = json.loads(result_file.read_text(encoding="utf-8"))
                    if data.get("id") == signal_id:
                        return data
            except Exception:
                pass
            await asyncio.sleep(1)
        return None

    def _bridge_account(self) -> dict:
        try:
            return json.loads((BRIDGE_DIR / "account.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _bridge_positions(self) -> list:
        try:
            data = json.loads((BRIDGE_DIR / "positions.json").read_text(encoding="utf-8"))
            return [_Position(p) for p in data]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    async def submit_order(self,
                           symbol: str,
                           direction: int,
                           volume: float,
                           entry_price: float,
                           stop_loss: float,
                           take_profit: float,
                           confidence: float = 0.0) -> Optional[int]:
        if direction not in (1, -1):
            raise ValueError(f"direction must be 1 or -1, got {direction}")
        if any(v <= 0 for v in [volume, entry_price, stop_loss, take_profit]):
            raise ValueError("volume and prices must be positive")

        if not self.mt5_initialized:
            self.logger.error("Cannot submit order: MT5 not initialized")
            return None

        now = time.time()
        if symbol in self._last_trade_time:
            elapsed = now - self._last_trade_time[symbol]
            if elapsed < TRADE_COOLDOWN_SECONDS:
                self.logger.debug(f"Cooldown {symbol}: {int(TRADE_COOLDOWN_SECONDS-elapsed)}s left")
                return None

        trade_check = self.risk_mgr.can_open_trade(symbol, float(volume), self.open_positions)
        if not trade_check["valid"]:
            # Ranked Replacement only makes sense for the global portfolio
            # cap: it frees a slot by closing the globally lowest-confidence
            # position, regardless of symbol. Per-symbol caps ("Max trades
            # for X", "Max volume for X") aren't resolved by closing an
            # unrelated symbol's position, so don't trigger it for those -
            # a broader "max" substring match used to catch them too.
            if trade_check["reason"] == "Global max open trades reached" and self.open_positions:
                lowest_id = min(self.open_positions, key=lambda k: self.open_positions[k].get("confidence", 1.0))
                lowest_pos = self.open_positions[lowest_id]
                hold_minutes = (datetime.now() - lowest_pos["opened_at"]).total_seconds() / 60
                confidence_gap = confidence - lowest_pos.get("confidence", 1.0)
                held_long_enough = hold_minutes >= self.risk_mgr.config.min_replacement_hold_minutes
                confident_enough = confidence_gap >= self.risk_mgr.config.min_replacement_confidence_gap

                # Never realise a loss on a position just to make room for a
                # new signal, no matter how much stronger that signal is. If
                # the live P&L can't be confirmed, treat it as not-eligible
                # rather than risk closing a losing position on bad data —
                # the same class of bug that caused a real -$62.27 loss to
                # get replaced based on a broken hold-time reading earlier.
                current_profit = self._get_live_profit(lowest_id)
                not_losing = current_profit is not None and current_profit >= self.risk_mgr.config.min_replacement_profit

                if held_long_enough and confident_enough and not_losing:
                    self.logger.info(f"Ranked replacement: closing #{lowest_id}")
                    await self.close_position(lowest_id, "Ranked Replacement")
                    trade_check = self.risk_mgr.can_open_trade(symbol, float(volume), self.open_positions)
                    if not trade_check["valid"]:
                        return None
                else:
                    profit_str = "unknown" if current_profit is None else f"${current_profit:+.2f}"
                    self.logger.warning(
                        f"Trade blocked: {trade_check['reason']} (replacement rejected for #{lowest_id} — "
                        f"hold {hold_minutes:.1f}/{self.risk_mgr.config.min_replacement_hold_minutes:.1f}min, "
                        f"confidence gap {confidence_gap:.2f}/{self.risk_mgr.config.min_replacement_confidence_gap:.2f}, "
                        f"profit {profit_str}"
                        f")"
                    )
                    return None
            else:
                self.logger.warning(f"Trade blocked: {trade_check['reason']}")
                # Per-symbol/volume caps are routine and fire repeatedly
                # whenever a symbol that already has a position keeps
                # generating signals - alerting on every occurrence just
                # spams Telegram with no new information each time. The
                # account-wide circuit breakers are rare and important
                # enough to still page immediately.
                if trade_check["reason"] in ("Daily loss limit reached", "Max drawdown reached"):
                    await self.telegram.send_alert(f"⛔ Trade blocked: {trade_check['reason']}")
                return None

        validation = self.risk_mgr.validate_trade_setup(entry_price, stop_loss, take_profit)
        if not validation["valid"]:
            self.logger.warning(f"Invalid setup: {validation['reason']}")
            return None

        # --- Spread check (IPC mode only) ---
        if not self._bridge_mode and MT5_AVAILABLE and symbol in SYMBOLS:
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                live_spread = tick.ask - tick.bid
                max_spread  = SYMBOLS[symbol].get("max_spread", float("inf"))
                if live_spread > max_spread:
                    self.logger.warning(f"Spread too wide {symbol}: {live_spread:.5f} > {max_spread:.5f}")
                    return None

        # --- Margin check ---
        if not self._bridge_mode and MT5_AVAILABLE:
            order_type = mt5.ORDER_TYPE_BUY if direction > 0 else mt5.ORDER_TYPE_SELL
            acc = mt5.account_info()
            if acc:
                margin_req = mt5.order_calc_margin(order_type, symbol, float(volume), float(entry_price))
                if margin_req and margin_req > acc.margin_free * 0.8:
                    safe_vol = round(max(0.01, float(volume) * (acc.margin_free * 0.8 / margin_req)), 2)
                    self.logger.warning(f"Volume reduced {volume}→{safe_vol} (margin)")
                    volume = safe_vol
        else:
            acc_data = self._bridge_account()
            if acc_data:
                free = acc_data.get("free_margin", 999999)
                if free < 50:
                    self.logger.warning(f"Low free margin ({free:.2f}), skipping")
                    return None

        # --- Send order ---
        dir_str = "BUY" if direction > 0 else "SELL"

        if self._bridge_mode:
            sig_id = str(uuid.uuid4())[:8]
            self._bridge_write({
                "id":      sig_id,
                "action":  dir_str,
                "symbol":  symbol,
                "volume":  round(float(volume), 2),
                "entry":   round(float(entry_price), 5),
                "sl":      round(float(stop_loss),   5),
                "tp":      round(float(take_profit),  5),
            })
            self.logger.info(f"Bridge signal sent [{sig_id}]: {symbol} {dir_str}")
            result = await self._bridge_wait_result(sig_id)
            if result is None or result.get("status") != "OK":
                err = result.get("error", "timeout") if result else "timeout"
                self.logger.error(f"Bridge order failed: {err}")
                await self.telegram.send_alert(f"❌ Order failed ({symbol}): {err}")
                return None
            order_id = int(result["ticket"])
        else:
            order_type = mt5.ORDER_TYPE_BUY if direction > 0 else mt5.ORDER_TYPE_SELL
            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       float(volume),
                "type":         order_type,
                "price":        float(entry_price),
                "sl":           float(stop_loss),
                "tp":           float(take_profit),
                "deviation":    5,
                "magic":        123456,
                "comment":      f"AI-{int(time.time())}",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            }
            res = mt5.order_send(request)
            if res.retcode != mt5.TRADE_RETCODE_DONE:
                self.logger.error(f"Order failed: {res.comment}")
                await self.telegram.send_alert(f"❌ Order failed: {res.comment}")
                return None
            order_id = res.order

        self.open_positions[order_id] = {
            "symbol":     symbol,
            "direction":  direction,
            "volume":     volume,
            "entry":      entry_price,
            "sl":         stop_loss,
            "tp":         take_profit,
            "opened_at":  datetime.now(),
            "order_id":   order_id,
            "confidence": confidence,
        }
        self._last_trade_time[symbol] = time.time()
        self.logger.info(f"Order #{order_id} opened: {symbol} {dir_str} {volume}L @ {entry_price:.5f}")
        await self.telegram.send_alert(
            f"✅ <b>{symbol}</b> {dir_str}\n"
            f"Vol: {volume} | Entry: {entry_price:.5f}\n"
            f"SL: {stop_loss:.5f} | TP: {take_profit:.5f}\n"
            f"R:R: {validation['ratio']:.2f}:1"
        )
        return order_id

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    async def get_closed_pnl(self, ticket: int, lookback_days: int = 1,
                              max_attempts: int = 5, base_delay: float = 0.5) -> Optional[float]:
        """Poll MT5 deal history for the realised profit of a closed position.

        The exit deal isn't always registered in history_deals_get the
        instant order_send returns, so this retries with backoff instead of
        trusting a single lookup. Returns None (never 0.0) when a genuine
        close can't be confirmed, so callers don't mistake a lookup failure
        for an actual zero P&L and corrupt daily P&L tracking with it.

        Specifically waits for an exit-type deal (entry != 0), not just any
        deal for this position: the entry deal is already in history from
        when the position opened, so treating "any deal found" as success
        returned a stale profit=0.0 from the entry alone, before the real
        closing deal had registered - a live GBPUSD close that actually
        lost $62.27 got logged (and fed into daily P&L) as $0.00 this way.

        The query window's upper bound is padded several hours past "now"
        because this account's deal timestamps run consistently ahead of
        true UTC (observed ~3h) - too tight a bound silently excludes the
        very-recent deal being polled for.
        """
        if not MT5_AVAILABLE or self._bridge_mode:
            return None
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        until = datetime.now(timezone.utc) + timedelta(hours=6)
        for attempt in range(1, max_attempts + 1):
            try:
                deals = mt5.history_deals_get(since, until)
                matched = [d for d in (deals or []) if d.position_id == ticket]
                exit_deals = [d for d in matched if d.entry != 0]
                if exit_deals:
                    return float(sum(d.profit for d in matched))
            except Exception as e:
                self.logger.warning(f"P&L lookup failed for #{ticket} (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                await asyncio.sleep(base_delay * attempt)
        self.logger.error(
            f"Could not confirm P&L for closed position #{ticket} after {max_attempts} attempts "
            f"— deal not found in MT5 history"
        )
        return None

    def queue_pnl_reconciliation(self, order_id: int, symbol: str, reason: str) -> None:
        """Register a closed position whose P&L couldn't be confirmed so
        reconcile_pending_pnl() keeps retrying it on later main-loop ticks."""
        self.pending_pnl[order_id] = {
            "symbol":    symbol,
            "reason":    reason,
            "queued_at": datetime.now(),
            "attempts":  0,
        }

    async def reconcile_pending_pnl(self) -> None:
        """Retry P&L confirmation for positions queued by queue_pnl_reconciliation.

        Meant to be called once per main-loop tick. On success it backfills
        risk_mgr.daily_pnl (which was skipped at close time) and the trade_log
        entry. On repeated failure it escalates to a loud Telegram alert every
        PNL_RECONCILE_ALERT_INTERVAL attempts — a daily-loss circuit breaker
        silently running on incomplete P&L data is a bigger risk than a noisy
        alert.
        """
        for order_id in list(self.pending_pnl):
            entry = self.pending_pnl[order_id]
            pnl = await self.get_closed_pnl(order_id)

            if pnl is not None:
                self.risk_mgr.update_daily_pnl(pnl)
                for t in self.trade_log:
                    if t["order_id"] == order_id:
                        t["pnl"] = pnl
                        break
                del self.pending_pnl[order_id]
                self.logger.info(
                    f"Reconciled P&L for #{order_id} ({entry['symbol']}): ${pnl:+.2f} "
                    f"| Daily P&L: ${self.risk_mgr.daily_pnl:+.2f}"
                )
                await self.telegram.send_alert(
                    f"✅ P&L reconciled — <b>{entry['symbol']}</b> #{order_id}: ${pnl:+.2f}\n"
                    f"Daily P&L: ${self.risk_mgr.daily_pnl:+.2f}"
                )
                continue

            entry["attempts"] += 1
            stuck_minutes = (datetime.now() - entry["queued_at"]).total_seconds() / 60
            if entry["attempts"] % PNL_RECONCILE_ALERT_INTERVAL == 1:
                self.logger.error(
                    f"P&L still unconfirmed for #{order_id} ({entry['symbol']}) after "
                    f"{entry['attempts']} reconciliation attempts ({stuck_minutes:.1f} min) "
                    f"— daily P&L / daily-loss breaker may be running on incomplete data"
                )
                await self.telegram.send_alert(
                    f"⚠️ <b>P&L UNCONFIRMED</b> — {entry['symbol']} #{order_id} ({entry['reason']})\n"
                    f"{entry['attempts']} reconciliation attempts over {stuck_minutes:.1f} min.\n"
                    f"Daily P&L / daily-loss limit may be computed on incomplete data until this resolves."
                )

    async def close_position(self, order_id: int, reason: str = "Manual") -> bool:
        if not self.mt5_initialized or order_id not in self.open_positions:
            return False

        pos = self.open_positions[order_id]
        close_price = 0.0

        if self._bridge_mode:
            sig_id = str(uuid.uuid4())[:8]
            self._bridge_write({"id": sig_id, "action": "CLOSE",
                                "symbol": pos["symbol"], "ticket": order_id,
                                "volume": pos["volume"], "entry": 0, "sl": 0, "tp": 0})
            # Fire-and-forget — we can't easily await here without making this async
            time.sleep(6)
            ok = True
        else:
            tick = mt5.symbol_info_tick(pos["symbol"])
            if not tick:
                return False
            close_type  = mt5.ORDER_TYPE_SELL if pos["direction"] > 0 else mt5.ORDER_TYPE_BUY
            close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       pos["symbol"],
                "volume":       pos["volume"],
                "type":         close_type,
                "position":     order_id,
                "price":        close_price,
                "deviation":    5,
                "magic":        123456,
                "comment":      f"Close {order_id}",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            }
            res = mt5.order_send(request)
            ok  = res and res.retcode == mt5.TRADE_RETCODE_DONE
            # Give MT5 a moment to register the deal before reading history
            await asyncio.sleep(1)

        if ok:
            pnl = await self.get_closed_pnl(order_id)
            if pnl is None:
                self.logger.error(
                    f"Position #{order_id} closed ({reason}) but P&L is unconfirmed — "
                    f"NOT updating daily P&L, recording as unknown and queuing for reconciliation"
                )
                self.queue_pnl_reconciliation(order_id, pos["symbol"], reason)
            else:
                self.risk_mgr.update_daily_pnl(pnl)
            self.trade_log.append({
                "order_id": order_id, "symbol": pos["symbol"],
                "entry":    pos["entry"], "exit": close_price,
                "volume":   pos["volume"], "pnl": pnl,
                "reason":   reason,
                "duration": (datetime.now() - pos["opened_at"]).total_seconds() / 3600,
            })
            del self.open_positions[order_id]

            dir_str  = "BUY" if pos["direction"] > 0 else "SELL"
            if pnl is None:
                self.logger.info(f"Position #{order_id} closed ({reason}) | P&L: unknown")
                pnl_str  = "unknown"
                emoji    = "⚪"
            else:
                self.logger.info(f"Position #{order_id} closed ({reason}) | P&L: ${pnl:+.2f}")
                pnl_str  = f"${pnl:+.2f}"
                emoji    = "🟢" if pnl >= 0 else "🔴"
            exit_str = f"{close_price:.5f}" if close_price else "N/A"
            await self.telegram.send_alert(
                f"{emoji} <b>{pos['symbol']}</b> {dir_str} closed ({reason})\n"
                f"Entry: {pos['entry']:.5f} | Exit: {exit_str}\n"
                f"P&L: {pnl_str}"
            )
        return ok

    def modify_position_sl(self, order_id: int, new_sl: float) -> bool:
        if not self.mt5_initialized or order_id not in self.open_positions:
            return False
        pos = self.open_positions[order_id]

        if self._bridge_mode:
            sig_id = str(uuid.uuid4())[:8]
            self._bridge_write({
                "id": sig_id, "action": "MODIFY_SL",
                "symbol": pos["symbol"], "ticket": order_id,
                "sl": round(float(new_sl), 5), "tp": round(float(pos["tp"]), 5),
                "volume": 0, "entry": 0,
            })
            time.sleep(6)
            pos["sl"] = new_sl
            return True
        else:
            request = {
                "action":   mt5.TRADE_ACTION_SLTP,
                "position": order_id,
                "symbol":   pos["symbol"],
                "sl":       float(new_sl),
                "tp":       float(pos["tp"]),
            }
            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                pos["sl"] = new_sl
                return True
            self.logger.warning(f"SL modify failed #{order_id}: {res.comment if res else 'no result'}")
            return False

    def get_open_positions(self) -> list:
        if not self.mt5_initialized:
            return []
        if self._bridge_mode:
            return self._bridge_positions()
        positions = mt5.positions_get()
        return list(positions) if positions else []

    def _get_live_profit(self, ticket: int) -> Optional[float]:
        """Look up a position's current floating profit/loss from MT5."""
        try:
            for pos in self.get_open_positions():
                if pos.ticket == ticket:
                    return float(pos.profit)
        except Exception as e:
            self.logger.warning(f"Could not fetch live profit for #{ticket}: {e}")
        return None

    def get_account_info(self) -> Dict:
        if not self.mt5_initialized:
            return {}
        if self._bridge_mode:
            d = self._bridge_account()
            if not d:
                return {}
            return {
                "balance":        d.get("balance", 0),
                "equity":         d.get("equity",  0),
                "profit":         d.get("equity",  0) - d.get("balance", 0),
                "margin_free":    d.get("free_margin", 0),
                "margin_level":   0,
                "open_positions": len(self.open_positions),
            }
        acc = mt5.account_info()
        if not acc:
            return {}
        return {
            "balance":        acc.balance,
            "equity":         acc.equity,
            "profit":         acc.profit,
            "margin_free":    acc.margin_free,
            "margin_level":   acc.margin_level,
            "open_positions": len(self.open_positions),
        }
