import asyncio
import logging
from typing import Dict
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.app = None
        self.logger = logging.getLogger('TelegramNotifier')
    
    async def initialize(self):
        """Initialize the Telegram bot application"""
        self.app = Application.builder().token(self.bot_token).build()
        await self.app.initialize()
        self.logger.info("Telegram Notifier initialized")
    
    async def send_alert(self, message: str):
        """Send a trade alert to Telegram"""
        try:
            if not self.app:
                return
            await self.app.bot.send_message(chat_id=self.chat_id, text=message, parse_mode='HTML')
        except Exception as e:
            self.logger.error(f"Telegram send failed: {e}")
    
    async def send_daily_report(self, report: Dict):
        """Send end-of-day P&L report"""
        msg = f"""
📊 <b>DAILY REPORT</b>
Date: {report.get('date')}
P&L: ${report.get('pnl', 0):.2f} ({report.get('pnl_pct', 0):.2f}%)
Trades: {report.get('total_trades', 0)} (W: {report.get('wins', 0)}, L: {report.get('losses', 0)})
Win Rate: {report.get('win_rate', 0):.1%}
Largest Win: ${report.get('max_win', 0):.2f}
Largest Loss: ${report.get('max_loss', 0):.2f}
Equity: ${report.get('equity', 0):.2f}
        """
        await self.send_alert(msg)
    
    async def setup_controls(self, trader):
        """Add command handlers to allow user interaction"""
        if not self.app:
            return
            
        # Store trader reference in bot_data so handlers can access it
        self.app.bot_data['trader'] = trader
        
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("positions", self.cmd_positions))
        self.app.add_handler(CommandHandler("close", self.cmd_close_position))
        self.app.add_handler(CommandHandler("stop", self.cmd_emergency_stop))
        
        try:
            await asyncio.wait_for(self.app.start(), timeout=15)
            await asyncio.wait_for(self.app.updater.start_polling(), timeout=15)
            self.logger.info("Telegram controls active")
        except asyncio.TimeoutError:
            self.logger.error("Telegram setup timed out after 15s — running without live controls")
        except Exception as e:
            self.logger.error(f"Telegram setup failed: {e}")

    def _is_authorized(self, update: Update) -> bool:
        """Single-operator bot: the only authorized chat is the one this
        notifier was configured to alert (self.chat_id). No multi-user
        allowlist needed for a personal bot."""
        chat = update.effective_chat
        return chat is not None and str(chat.id) == str(self.chat_id)

    async def shutdown(self):
        """Shutdown the Telegram bot cleanly"""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            self.logger.info("Telegram Notifier shut down")
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            self.logger.warning(f"Unauthorized /status attempt from chat {update.effective_chat.id if update.effective_chat else '?'}")
            return

        trader = context.bot_data.get('trader')
        if not trader:
            return
            
        account_info = trader.get_account_info()
        msg = f"""
💰 <b>ACCOUNT STATUS</b>
Balance: ${account_info.get('balance', 0):.2f}
Equity: ${account_info.get('equity', 0):.2f}
Floating P&L: ${account_info.get('profit', 0):.2f}
Free Margin: ${account_info.get('margin_free', 0):.2f}
Margin Level: {account_info.get('margin_level', 0):.0f}%
Open Trades: {account_info.get('open_positions', 0)}
        """
        await update.message.reply_text(msg, parse_mode='HTML')
    
    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            self.logger.warning(f"Unauthorized /positions attempt from chat {update.effective_chat.id if update.effective_chat else '?'}")
            return

        trader = context.bot_data.get('trader')
        if not trader:
            return
            
        positions = trader.open_positions
        if not positions:
            await update.message.reply_text("No open positions")
            return
        
        msg = "<b>OPEN POSITIONS</b>\n"
        for order_id, pos in positions.items():
            msg += f"\n#{order_id}: {pos['symbol']} {pos['volume']}L\nEntry: {pos['entry']:.5f}\nSL: {pos['sl']:.5f} | TP: {pos['tp']:.5f}"
        
        await update.message.reply_text(msg, parse_mode='HTML')
    
    async def cmd_close_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            self.logger.warning(f"Unauthorized /close attempt from chat {update.effective_chat.id if update.effective_chat else '?'}")
            return

        if not context.args:
            await update.message.reply_text("Usage: /close <order_id>")
            return
        
        order_id = int(context.args[0])
        trader = context.bot_data.get('trader')
        if not trader:
            return
        
        if await trader.close_position(order_id, "Manual close via Telegram"):
            await update.message.reply_text(f"✅ Position #{order_id} closed")
        else:
            await update.message.reply_text(f"❌ Could not close position #{order_id}")
    
    async def cmd_emergency_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            self.logger.warning(f"Unauthorized /stop attempt from chat {update.effective_chat.id if update.effective_chat else '?'}")
            return

        trader = context.bot_data.get('trader')
        if not trader:
            return
        
        closed_count = 0
        # Iterate over a list of keys since we modify the dict during iteration
        for order_id in list(trader.open_positions.keys()):
            if await trader.close_position(order_id, "EMERGENCY STOP"):
                closed_count += 1
        
        await update.message.reply_text(f"🛑 EMERGENCY STOP: {closed_count} positions closed")
