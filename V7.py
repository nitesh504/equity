import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext
import threading
import datetime
import json
import os
import csv
import sys
import asyncio
import time
from ib_insync import *

def detect_engulfing_signal(candles):
    if len(candles) < 7:
        return None

    recent_candles = candles[-7:]

    def extract(c):
        opens = [float(candle.open) for candle in c]
        highs = [float(candle.high) for candle in c]
        lows = [float(candle.low) for candle in c]
        closes = [float(candle.close) for candle in c]
        volumes = [int(candle.volume) for candle in c]
        return opens, highs, lows, closes, volumes

    opens, highs, lows, closes, volumes = extract(recent_candles)

    o1, o2, o3, o4, o5, o6, o7 = opens
    h1, h2, h3, h4, h5, h6, h7 = highs
    l1, l2, l3, l4, l5, l6, l7 = lows
    c1, c2, c3, c4, c5, c6, c7 = closes
    v1, v2, v3, v4, v5, v6, v7 = volumes

    # Buy signal condition
    if c7 >= c6 and (c6 >= c1 or c6 >= c3 or c6 >= c5) and c5 <= c4 and v7 >= v6 * 0.65 and v6 >= v5 * 0.65:
        return 'BUY'

    # Sell signal condition
    if c7 <= c1 and c7 <= c2 and c3 >= c3 and c7 <= c5 and v7 >= v6 * 0.85 and v7 >= v5 * 0.8:
        return 'SELL'

    return None

class TradingBotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title('Equitor')
        self.stock_list = ['SPY', 'QQQ','AAPL','TSLA','JPM','MSFT','GOOGL','NVDA','INTC']
        self.trades_today = []
        self.executed_trades_count = 0
        self.export_file = 'trades_export.csv'
        self.running = False
        self.last_trade_day = datetime.datetime.now().date()
        self.ib = None
        self.scanning_start_time = None
        self.trading_stopped_by_limits = False  # Flag to prevent further trading
        self.closing_positions = False  # NEW: Flag to prevent multiple close operations
        self.build_main_gui()

    def clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def build_main_gui(self):
        self.clear_window()

        # Connection settings
        tk.Label(self.root, text='IB IP:').grid(row=0, column=0)
        self.ip_entry = tk.Entry(self.root)
        self.ip_entry.insert(0, '127.0.0.1')
        self.ip_entry.grid(row=0, column=1)

        tk.Label(self.root, text='PORT:').grid(row=0, column=2)
        self.port_entry = tk.Entry(self.root)
        self.port_entry.insert(0, '7497')
        self.port_entry.grid(row=0, column=3)

        tk.Label(self.root, text='Client ID:').grid(row=0, column=4)
        self.client_id_entry = tk.Entry(self.root)
        self.client_id_entry.insert(0, '1')
        self.client_id_entry.grid(row=0, column=5)

        # Stock list
        self.tree = ttk.Treeview(self.root, columns='Symbol', show='headings')
        self.tree.heading('Symbol', text='Stock Symbol')
        self.tree.grid(row=1, column=0, columnspan=6, pady=10)

        for stock in self.stock_list:
            self.tree.insert('', tk.END, values=(stock,))

        # Stock controls
        self.stock_entry = tk.Entry(self.root)
        self.stock_entry.grid(row=2, column=0)

        tk.Button(self.root, text='Add Stock', command=self.add_stock).grid(row=2, column=1)
        tk.Button(self.root, text='Delete Stock', command=self.remove_stock).grid(row=2, column=2)

        # Trading parameters
        tk.Label(self.root, text='Profit Amount ($):').grid(row=3, column=0)
        self.profit_amt_entry = tk.Entry(self.root)
        self.profit_amt_entry.insert(0, '2')
        self.profit_amt_entry.grid(row=3, column=1)

        tk.Label(self.root, text='Stop Loss Amount ($):').grid(row=3, column=2)
        self.sl_amt_entry = tk.Entry(self.root)
        self.sl_amt_entry.insert(0, '1')
        self.sl_amt_entry.grid(row=3, column=3)

        # Log area
        self.log_area = scrolledtext.ScrolledText(self.root, width=80, height=10)
        self.log_area.grid(row=4, column=0, columnspan=6, pady=10)

        # Status
        self.status_label = tk.Label(self.root, text='No trades today.')
        self.status_label.grid(row=5, column=0, columnspan=6)

        # Portfolio limits info
        limits_info = tk.Label(self.root, text='Portfolio Limits: Stop Loss $500 | Take Profit $1000', 
                              fg='blue', font=('Arial', 9, 'bold'))
        limits_info.grid(row=6, column=0, columnspan=6, pady=5)

        # Control buttons
        tk.Button(self.root, text='Start Bot', command=self.start_bot).grid(row=7, column=0)
        tk.Button(self.root, text='Stop Bot', command=self.stop_bot).grid(row=7, column=1)
        
        # UPDATED: Store reference to the close button to update its state
        self.close_all_button = tk.Button(self.root, text='Close All Positions', 
                                         command=self.manual_close_all, 
                                         bg='red', fg='white', font=('Arial', 9, 'bold'))
        self.close_all_button.grid(row=7, column=2)

    def add_stock(self):
        stock = self.stock_entry.get().strip().upper()
        if stock:
            self.tree.insert('', tk.END, values=(stock,))
            self.stock_entry.delete(0, tk.END)

    def remove_stock(self):
        selected_items = self.tree.selection()
        for item in selected_items:
            self.tree.delete(item)

    def manual_close_all(self):
        """UPDATED: Manual button to close all positions - now runs in separate thread"""
        if self.closing_positions:
            self.log("⚠️ Close operation already in progress. Please wait...")
            return
            
        if not self.ib or not self.ib.isConnected():
            self.log("❌ Not connected to IB. Cannot close positions.")
            messagebox.showerror("Error", "Not connected to Interactive Brokers")
            return
        
        # Confirm with user
        result = messagebox.askyesno("Confirm Close All", 
                                   "Are you sure you want to close ALL positions?\n\nThis action cannot be undone.",
                                   icon='warning')
        if not result:
            return
        
        # Update button state and start close operation in separate thread
        self.closing_positions = True
        self.close_all_button.config(text='Closing...', state='disabled', bg='orange')
        self.trading_stopped_by_limits = True  # Stop further trading
        
        # Run close operation in separate thread to avoid blocking GUI
        threading.Thread(target=self._close_all_positions_thread, daemon=True).start()

    def _close_all_positions_thread(self):
        """NEW: Thread worker for closing positions with proper event loop setup"""
        try:
            # Set up event loop for this thread (required for ib_insync)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                success = self.close_all_positions_and_orders("Manual close all positions requested.")
                
                # Update GUI in main thread
                self.root.after(0, self._close_operation_completed, success)
                
            finally:
                # Clean up the event loop
                try:
                    loop.close()
                except Exception as loop_error:
                    self.log(f"Error closing event loop: {loop_error}")
            
        except Exception as e:
            self.log(f"🔥 Error in close positions thread: {e}")
            import traceback
            self.log(f"🔍 Traceback: {traceback.format_exc()}")
            self.root.after(0, self._close_operation_completed, False)

    def _close_operation_completed(self, success):
        """NEW: Called when close operation completes - runs in main thread"""
        self.closing_positions = False
        
        if success:
            self.close_all_button.config(text='Close All Positions', state='normal', bg='red')
            self.log("✅ Close All Positions operation completed successfully")
            messagebox.showinfo("Success", "All positions have been closed successfully!")
        else:
            self.close_all_button.config(text='Close All Positions', state='normal', bg='red')
            self.log("⚠️ Close All Positions operation completed with some issues")
            messagebox.showwarning("Warning", "Close operation completed but some positions may still be open. Check the log for details.")

    def start_bot(self):
        if not self.running:
            self.running = True
            self.trading_stopped_by_limits = False  # Reset the flag when starting
            self.closing_positions = False  # NEW: Reset close flag
            self.scanning_start_time = time.time()
            self.log('Bot started.')
            # Start real-time status update thread
            threading.Thread(target=self.update_scanning_status, daemon=True).start()
            # Start main trading logic thread
            threading.Thread(target=self.run_trading_logic, daemon=True).start()

    def stop_bot(self):
        self.running = False
        self.scanning_start_time = None
        self.trading_stopped_by_limits = False  # Reset flag when stopping
        self.closing_positions = False  # NEW: Reset close flag
        self.log('Bot stopped.')
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()

    def update_scanning_status(self):
        """Update scanning status with real-time timer"""
        while self.running and self.scanning_start_time:
            try:
                elapsed_seconds = int(time.time() - self.scanning_start_time)
                hours = elapsed_seconds // 3600
                minutes = (elapsed_seconds % 3600) // 60
                seconds = elapsed_seconds % 60
                
                time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                
                # Update the last line in log area if it contains "BOT is scanning"
                content = self.log_area.get("1.0", tk.END)
                lines = content.strip().split('\n')
                
                if lines and "BOT is scanning" in lines[-1]:
                    # Remove the last line and add updated one
                    self.log_area.delete("end-2c linestart", "end-1c")
                    current_time = datetime.datetime.now().strftime('%H:%M:%S')
                    self.log_area.insert(tk.END, f"{current_time} - BOT is scanning for trades... (Running: {time_str})\n")
                    self.log_area.see(tk.END)
                
                time.sleep(1)
                
            except Exception as e:
                break

    def log(self, msg):
        """UPDATED: Thread-safe logging method"""
        def _log_to_gui():
            try:
                self.log_area.insert(tk.END, f"{datetime.datetime.now().strftime('%H:%M:%S')} - {msg}\n")
                self.log_area.see(tk.END)
                self.root.update_idletasks()  # Force GUI update
            except Exception as e:
                print(f"Error logging to GUI: {e}")
        
        # If called from main thread, log directly; otherwise schedule it
        try:
            if threading.current_thread() == threading.main_thread():
                _log_to_gui()
            else:
                self.root.after(0, _log_to_gui)
        except Exception as e:
            print(f"Logging error: {msg} - {e}")

    def close_all_positions_and_orders(self, reason=""):
    try:
        self.log(f" Initiating COMPLETE portfolio closure: {reason}")

        if not self.ib or not self.ib.isConnected():
            self.log(" Not connected to IB - cannot close positions")
            return False

        # Step 1: Cancel all working orders
        self.log("📋Step 1: Cancelling all open orders...")
        self.ib.reqGlobalCancel()
        self.ib.waitOnUpdate(timeout=2)

        # Step 2: Fetch positions
        self.log(" Step 2: Fetching current positions...")
        positions = [p for p in self.ib.positions() if p.position != 0]
        if not positions:
            self.log(" No open positions to close")
            return True

        trades = []
        for pos in positions:
            try:
                [contract] = self.ib.qualifyContracts(pos.contract)

                qty = abs(int(pos.position))
                action = "SELL" if pos.position > 0 else "BUY"

                # FIX: Pass outsideRth=True inside constructor
                order = MarketOrder(action, qty, outsideRth=True)

                trade = self.ib.placeOrder(contract, order)
                trades.append(trade)
                self.log(f" Submitted {action} {qty} {contract.symbol} @ MKT")

            except Exception as e:
                self.log(f" Error closing {pos.contract.symbol}: {e}")

        # Step 3: Wait for fills
        self.log("Step 3: Waiting for fills...")
        deadline = time.time() + 20
        while time.time() < deadline:
            self.ib.waitOnUpdate(timeout=1)  # process events
            all_filled = all(t.isDone() for t in trades)
            if all_filled:
                break

        # Step 4: Verify positions again
        remaining = [p for p in self.ib.positions() if p.position != 0]
        if remaining:
            self.log(" Some positions remain open after MARKET close attempt:")
            for p in remaining:
                self.log(f"   • {p.contract.symbol}: {p.position}")
            return False

        self.log(" ALL POSITIONS SUCCESSFULLY CLOSED")
        return True

    except Exception as e:
        self.log(f"Critical error in close_all_positions_and_orders: {e}")
        import traceback
        self.log(traceback.format_exc())
        return False


    def get_total_pnl(self):
        """Calculate total portfolio P&L more reliably"""
        try:
            total_pnl = 0.0
            positions = self.ib.positions()
            
            if not positions:
                return 0.0
            
            for position in positions:
                if position.position != 0:  # Only consider non-zero positions
                    try:
                        # Use unrealizedPNL if available, otherwise calculate manually
                        if hasattr(position, 'unrealizedPNL') and position.unrealizedPNL:
                            total_pnl += position.unrealizedPNL
                        else:
                            # Fallback: Get current market price and calculate
                            contract = position.contract
                            ticker = self.ib.reqMktData(contract, '', False, False)
                            self.ib.sleep(1)
                            
                            current_price = ticker.last or ticker.close or position.avgCost
                            
                            if current_price and current_price > 0:
                                position_pnl = (current_price - position.avgCost) * position.position
                                total_pnl += position_pnl
                            
                            # Cancel market data subscription
                            self.ib.cancelMktData(contract)
                    except Exception as e:
                        self.log(f"Error calculating P&L for {position.contract.symbol}: {e}")
                        continue
            
            return total_pnl
            
        except Exception as e:
            self.log(f"Error calculating total P&L: {e}")
            return 0.0

    def write_trade_to_csv(self, trade):
        try:
            file_exists = os.path.isfile(self.export_file)
            with open(self.export_file, 'a', newline='') as f:
                writer = csv.writer(f)

                # Write header only if file is new
                if not file_exists:
                    writer.writerow(['symbol', 'action', 'entry', 'sl', 'tp', 'timestamp'])

                writer.writerow([
                    trade['symbol'],
                    trade['action'],
                    trade['entry'],
                    trade['sl'],
                    trade['tp'],
                    trade['timestamp']
                ])

            self.log(f"📄 Trade saved to {self.export_file}")

        except Exception as e:
            self.log(f"CSV write error: {e}")

    def check_portfolio_limits(self):
        try:
            # Skip if already closing positions
            if self.closing_positions:
                return False
                
            total_pnl = self.get_total_pnl()
            
            # Condition 1: Check for $500 loss limit
            if total_pnl <= -500:
                self.log(f"⚠️ PORTFOLIO STOP LOSS TRIGGERED: ${round(total_pnl, 2)}")
                self.closing_positions = True
                # Run in separate thread to avoid blocking
                threading.Thread(target=self._automated_close_thread, 
                               args=("Portfolio loss limit of $500 reached.",), daemon=True).start()
                return True
            
            # Condition 2: Check for $1000 profit limit
            if total_pnl >= 1000:
                self.log(f"🎯 PORTFOLIO TAKE PROFIT TRIGGERED: ${round(total_pnl, 2)}")
                self.closing_positions = True
                # Run in separate thread to avoid blocking
                threading.Thread(target=self._automated_close_thread, 
                               args=("Portfolio profit target of $1000 reached.",), daemon=True).start()
                return True
                
            return False
            
        except Exception as e:
            self.log(f"Error checking portfolio limits: {e}")
            self.closing_positions = False
            return False

    def check_end_of_day_close(self):
        """Check if it's time for end-of-day position closure (3:30 PM EST)"""
        try:
            # Skip if already closing positions
            if self.closing_positions:
                return False
                
            current_time = datetime.datetime.now().time()
            close_time = datetime.time(15, 30)  # 3:30 PM EST
            
            if current_time >= close_time:
                self.log("🕐 END OF DAY CLOSURE TRIGGERED (3:30 PM EST)")
                self.closing_positions = True
                # Run in separate thread to avoid blocking
                threading.Thread(target=self._automated_close_thread, 
                               args=("End of day position closure at 3:30 PM EST.",), daemon=True).start()
                return True
                
            return False
            
        except Exception as e:
            self.log(f"Error checking end of day close: {e}")
            self.closing_positions = False
            return False

    def _automated_close_thread(self, reason):
        """NEW: Thread worker for automated position closing"""
        try:
            # Set up event loop for this thread (required for ib_insync)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                success = self.close_all_positions_and_orders(reason)
                if success:
                    self.trading_stopped_by_limits = True
                
                # Reset closing flag
                self.closing_positions = False
                
            finally:
                # Clean up the event loop
                try:
                    loop.close()
                except Exception as loop_error:
                    self.log(f"Error closing event loop: {loop_error}")
            
        except Exception as e:
            self.log(f"🔥 Error in automated close thread: {e}")
            import traceback
            self.log(f"🔍 Traceback: {traceback.format_exc()}")
            self.closing_positions = False

    def run_trading_logic(self):
        """Run trading logic with portfolio limit checks"""
        # Set up event loop for this thread
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        except Exception as e:
            self.log(f'Event loop setup failed: {e}')
            self.running = False
            return

        host = self.ip_entry.get()
        port = int(self.port_entry.get())
        client_id = int(self.client_id_entry.get())

        stock_symbols = []
        for item_id in self.tree.get_children():
            stock_symbols.append(self.tree.item(item_id)['values'][0])

        # Use synchronous connection with event loop ready
        self.ib = IB()
        
        try:
            self.ib.connect(host, port, clientId=client_id, timeout=10)
            self.log(f'Connected to IB at {host}:{port}')
        except Exception as e:
            self.log(f'Connection failed: {e}')
            self.running = False
            return

        market_open = datetime.time(9, 30)
        market_close = datetime.time(16, 0)
        max_trades_per_day = 1000

        while self.running:
            try:
                current_date = datetime.datetime.now().date()

                # Reset trades for new day
                if current_date != self.last_trade_day:
                    self.trades_today = []
                    self.executed_trades_count = 0
                    self.trading_stopped_by_limits = False  # Reset limit flag for new day
                    self.closing_positions = False  # NEW: Reset close flag for new day
                    self.last_trade_day = current_date
                    self.log('Trade records reset for new day.')

                # Check market hours
                current_time = datetime.datetime.now().time()
                if current_time < market_open or current_time > market_close:
                    self.log('Outside trading hours.')
                    time.sleep(60)
                    continue

                # Skip trading if stopped by limits (but continue monitoring)
                if self.trading_stopped_by_limits:
                    self.log('Trading stopped by portfolio limits/EOD. Monitoring only...')
                    time.sleep(300)  # Check every 5 minutes but don't trade
                    continue

                # Skip if currently closing positions
                if self.closing_positions:
                    self.log('Position closing in progress. Waiting...')
                    time.sleep(30)
                    continue

                # Check all portfolio closure conditions
                # Condition 1 & 2: Portfolio limits (loss/profit)
                if self.check_portfolio_limits():
                    self.log("Portfolio limit reached - stopping trading for the day.")
                    break

                # Condition 3: End-of-day closure (3:30 PM EST)
                if self.check_end_of_day_close():
                    self.log("End of day closure completed - stopping trading for the day.")
                    break

                # Check max trades limit
                if self.executed_trades_count >= max_trades_per_day:
                    self.log('Max executed trades for the day reached.')
                    time.sleep(300)
                    continue

                # Reset scanning timer for this cycle
                self.scanning_start_time = time.time()

                # Continue with normal trading logic
                for symbol in stock_symbols:
                    if not self.running or self.trading_stopped_by_limits or self.closing_positions:
                        break

                    try:
                        # Get market data
                        contract = Stock(symbol, 'SMART', 'USD')
                        self.ib.qualifyContracts(contract)

                        # Get historical data
                        bars = self.ib.reqHistoricalData(
                            contract,
                            '',
                            '3600 S',  
                            '5 mins',
                            'TRADES',
                            True,
                            1
                        )

                        if not bars:
                            self.log(f"No data received for {symbol}")
                            continue

                        # Detect trading signal
                        signal = detect_engulfing_signal(bars)
                        self.log(f"{symbol} signal: {signal}")

                        if (self.running and signal in ('BUY', 'SELL') and 
                            not self.trading_stopped_by_limits and not self.closing_positions):
                            
                            sl_amount = float(self.sl_amt_entry.get())
                            tp_amount = float(self.profit_amt_entry.get())
                            qty = 100
                            current_price = float(bars[-1].close)

                            if signal == 'BUY':
                                sl_price = current_price - sl_amount
                                tp_price = current_price + tp_amount
                            else:  # SELL signal
                                sl_price = current_price + sl_amount
                                tp_price = current_price - tp_amount

                            # Place the bracket order
                            bracket_order = self.ib.bracketOrder(
                                'BUY' if signal == 'BUY' else 'SELL',
                                qty,
                                current_price,
                                tp_price,
                                sl_price
                            )

                            try:
                                for order in bracket_order:
                                    self.ib.placeOrder(contract, order)
                                
                                self.executed_trades_count += 1

                                # Record trade
                                trade = {
                                    'symbol': symbol,
                                    'action': signal,
                                    'entry': current_price,
                                    'sl': round(sl_price, 2),
                                    'tp': round(tp_price, 2),
                                    'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                }

                                self.write_trade_to_csv(trade)
                                self.log(f"{signal} order EXECUTED for {symbol} @ {current_price} | SL={trade['sl']} | TP={trade['tp']}")
                                self.log(f"{signal} order EXECUTED for {symbol} at {current_price} | SL: {round(sl_price, 2)} | TP: {round(tp_price, 2)}")
                                
                            except Exception as order_error:
                                self.log(f"Failed to execute {signal} order for {symbol}: {order_error}")

                    except Exception as e:
                        self.log(f"Error processing {symbol}: {e}")
                        continue

                # Update trade statistics
                self.update_trade_statistics()

                # Export trades to CSV - This method doesn't exist in the original code, commenting it out
                # self.export_trades()

                # Wait before next cycle with real-time scanning message
                if self.running and not self.trading_stopped_by_limits and not self.closing_positions:
                    self.log("BOT is scanning for trades...")
                    time.sleep(300)
                elif self.running:
                    time.sleep(300)  # Still wait but in monitoring mode

            except Exception as e:
                self.log(f"Main loop error: {e}")
                time.sleep(60)

        # Disconnect when stopped
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
            self.log('Disconnected from IB')
        
        # Close the event loop
        try:
            loop.close()
        except Exception as e:
            self.log(f'Error closing event loop: {e}')

    def update_trade_statistics(self):
        """Show trading status"""
        try:
            def _update_stats():
                if self.trading_stopped_by_limits:
                    self.status_label.config(text=f"Trading STOPPED - Executed Trades: {self.executed_trades_count}")
                elif self.closing_positions:
                    self.status_label.config(text=f"CLOSING POSITIONS - Executed Trades: {self.executed_trades_count}")
                else:
                    self.status_label.config(text=f"Executed Trades: {self.executed_trades_count}")
            
            # Ensure GUI update runs in main thread
            if threading.current_thread() == threading.main_thread():
                _update_stats()
            else:
                self.root.after(0, _update_stats)
                
        except Exception as e:
            self.log(f"Error updating statistics: {e}")

if __name__ == '__main__':
    root = tk.Tk()
    app = TradingBotGUI(root)
    root.mainloop()
