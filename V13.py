#Closd all positions + Cancelled Pending Orders

import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext
import threading
import datetime
import json
import os
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
    if c7 <= c1 and c7 <= c2 and c7 >= c3 and c7 <= c5 and v7 >= v6 * 0.85 and v7 >= v5 * 0.8:
        return 'SELL'

    return None

class TradingBotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title('Equitor')
        self.stock_list = ['SPY', 'QQQ','AAPL','JPM','MSFT','GOOGL','NVDA','CAT','AMD']
        #self.stock_list = ['SPY', 'QQQ','AAPL']
        self.trades_today = []
        self.executed_trades_count = 0  # Track only executed trades
        self.export_file = 'trades_export.csv'
        self.running = False
        self.last_trade_day = datetime.datetime.now().date()
        self.ib = None
        self.scanning_start_time = None  # Track scanning start time
        self.current_pnl = 0.0  # Store current daily PnL
        self.pnl_object = None  # Store the PnL object for real-time updates
        self.pnl_update_thread = None  # Thread for PnL updates
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
        self.profit_amt_entry.insert(0, '2')  # $2
        self.profit_amt_entry.grid(row=3, column=1)

        tk.Label(self.root, text='Stop Loss Amount ($):').grid(row=3, column=2)
        self.sl_amt_entry = tk.Entry(self.root)
        self.sl_amt_entry.insert(0, '1')  # $1
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

    def add_stock(self):
        stock = self.stock_entry.get().strip().upper()
        if stock:
            self.tree.insert('', tk.END, values=(stock,))
            self.stock_entry.delete(0, tk.END)

    def remove_stock(self):
        selected_items = self.tree.selection()
        for item in selected_items:
            self.tree.delete(item)

    def check_existing_position(self, symbol):
        """
        Check if there's already an active position for the given symbol.
        Returns: (has_position, position_size, position_type)
        - has_position: True if position exists, False otherwise
        - position_size: Size of the position (positive for long, negative for short)
        - position_type: 'LONG', 'SHORT', or 'NONE'
        """
        try:
            if not self.ib or not self.ib.isConnected():
                self.log(f"Cannot check position for {symbol}: not connected to IB")
                return False, 0, 'NONE'

            # Get all current positions
            positions = self.ib.positions()
            
            # Look for the specific symbol
            for pos in positions:
                # Check if this position matches our symbol
                if pos.contract.symbol.upper() == symbol.upper():
                    position_size = pos.position
                    
                    if position_size > 0:
                        return True, position_size, 'LONG'
                    elif position_size < 0:
                        return True, position_size, 'SHORT'
                    else:
                        # Position exists but size is 0 (shouldn't happen but handle it)
                        return False, 0, 'NONE'
            
            # No position found for this symbol
            return False, 0, 'NONE'
            
        except Exception as e:
            self.log(f"Error checking existing position for {symbol}: {e}")
            return False, 0, 'NONE'

    def close_all_positions(self, reason="Manual close all positions requested."):
        if reason:
            self.log(f"Closing all positions and cancelling pending orders: {reason}")
        try:
            # Method 1: Get all orders
            all_orders = self.ib.orders()
            
            # Method 2: Get open orders specifically
            open_orders = [order for order in all_orders if order.orderStatus.status in ['Submitted', 'PreSubmitted', 'PendingSubmit']]
            
            # Method 3: Also check trades that might have pending child orders
            open_trades = self.ib.openTrades()
            self.log(f"Found {len(all_orders)} total orders, {len(open_orders)} open orders, {len(open_trades)} open trades")
            cancelled_count = 0
            
            for order in all_orders:
                try:
                    if order.orderStatus.status in ['Submitted', 'PreSubmitted', 'PendingSubmit', 'ApiPending']:
                        self.ib.cancelOrder(order)
                        cancelled_count += 1
                        self.log(f"Cancelled order: {order.action} {order.totalQuantity} {order.orderStatus.status}")
                except Exception as e:
                    self.log(f"Failed to cancel order {order.orderId}: {e}")
            
            # Cancel from open trades (catches bracket order children)
            for trade in open_trades:
                try:
                    if trade.orderStatus.status in ['Submitted', 'PreSubmitted', 'PendingSubmit', 'ApiPending']:
                        self.ib.cancelOrder(trade.order)
                        cancelled_count += 1
                        self.log(f"Cancelled trade order: {trade.order.action} {trade.order.totalQuantity}")
                except Exception as e:
                    self.log(f"Failed to cancel trade order: {e}")
            
            self.log(f"Attempted to cancel {cancelled_count} orders/trades")
            
            # Wait longer for all cancellations to process
            self.ib.sleep(3)
            
            # Verify cancellations worked
            remaining_orders = self.ib.orders()
            active_remaining = [o for o in remaining_orders if o.orderStatus.status in ['Submitted', 'PreSubmitted', 'PendingSubmit']]
            
            if active_remaining:
                self.log(f"Warning: {len(active_remaining)} orders still pending after cancellation attempt")
                for order in active_remaining:
                    self.log(f"Remaining order: {order.action} {order.totalQuantity} {order.orderStatus.status}")
            else:
                self.log("All pending orders successfully cancelled")
                
        except Exception as e:
            self.log(f"Error in order cancellation process: {e}")
        
        # Step 2: Close all positions using SMART routing
        try:
            positions = self.ib.positions()
            if not positions:
                self.log("No open positions to close.")
                return

            trades = []

            for pos in positions:
                raw_contract = pos.contract
                position = pos.position

                # Force SMART routing for stocks
                contract = Stock(raw_contract.symbol, 'SMART', raw_contract.currency)
                [qualified_contract] = self.ib.qualifyContracts(contract)

                if position > 0:   # Long position -> sell to close
                    order = MarketOrder('SELL', position)
                    trade = self.ib.placeOrder(qualified_contract, order)
                    trades.append(trade)
                    self.log(f"Closing LONG {position} {qualified_contract.symbol} via SMART")

                elif position < 0: # Short position -> buy to close
                    order = MarketOrder('BUY', abs(position))
                    trade = self.ib.placeOrder(qualified_contract, order)
                    trades.append(trade)
                    self.log(f"Closing SHORT {abs(position)} {qualified_contract.symbol} via SMART")

            self.ib.sleep(1)
            self.log("All equity positions closed via SMART routing.")
            
        except Exception as e:
            self.log(f"Error closing positions: {e}")
        
        self.log("Position closing and order cancellation process completed.")

    def start_bot(self):
        if not self.running:
            self.running = True
            self.scanning_start_time = time.time()  # Record scanning start time
            self.log('Bot started.')
            # Start real-time status update thread
            threading.Thread(target=self.update_scanning_status, daemon=True).start()
            # Start main trading logic thread
            threading.Thread(target=self.run_trading_logic, daemon=True).start()

    def stop_bot(self):
        self.running = False
        self.scanning_start_time = None  # Reset scanning time
        self.log('Bot stopped.')
        
        # Stop PnL update thread
        if self.pnl_update_thread and self.pnl_update_thread.is_alive():
            self.pnl_update_thread = None
        
        if self.ib and self.ib.isConnected():
            # Cancel PnL subscription before disconnecting
            if self.pnl_object:
                try:
                    account = self.ib.managedAccounts()[0]
                    self.ib.cancelPnL(account, "")
                    self.pnl_object = None
                    self.log("PnL subscription cancelled.")
                except Exception as e:
                    self.log(f"Error cancelling PnL subscription: {e}")
            self.ib.disconnect()

    def update_scanning_status(self):
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
                
                time.sleep(1)  # Update every second
                
            except Exception as e:
                break

    def log(self, msg):
        self.log_area.insert(tk.END, f"{datetime.datetime.now().strftime('%H:%M:%S')} - {msg}\n")
        self.log_area.see(tk.END)

    def setup_pnl_subscription(self):
        """Set up PnL subscription using the working method from reference code"""
        try:
            if not self.ib or not self.ib.isConnected():
                self.log("Cannot set up PnL subscription: not connected to IB")
                return False

            # Get account ID
            account = self.ib.managedAccounts()[0]
            self.log(f"Setting up PnL subscription for account: {account}")

            # Request real-time account-wide PnL subscription
            self.pnl_object = self.ib.reqPnL(account, "")
            
            self.log("PnL subscription established successfully")
            return True
            
        except Exception as e:
            self.log(f"Error setting up PnL subscription: {e}")
            return False

    def start_pnl_updates(self):
        """Start the PnL update thread - Silent updates"""
        def pnl_update_loop():
            # Set up event loop for this thread
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            except Exception as e:
                self.log(f'PnL thread event loop setup failed: {e}')
                return
            
            try:
                while self.running and self.ib and self.ib.isConnected() and self.pnl_object:
                    try:
                        # Use waitOnUpdate
                        self.ib.waitOnUpdate(timeout=1)  # 1 second timeout
                        
                        if self.pnl_object.dailyPnL is not None:
                            # Update P&L silently without logging
                            self.current_pnl = float(self.pnl_object.dailyPnL)
                                
                    except Exception as e:
                        if self.running:  # Only log errors if we're still running
                            # Reduced error logging frequency to avoid spam
                            time.sleep(1)
                        else:
                            time.sleep(1)
                        
            except Exception as e:
                if self.running:
                    self.log(f"PnL update thread error: {e}")
            finally:
                # Close the event loop when done
                try:
                    loop.close()
                except Exception as e:
                    pass  # Silent cleanup
        
        # Start the PnL update thread
        self.pnl_update_thread = threading.Thread(target=pnl_update_loop, daemon=True)
        self.pnl_update_thread.start()
        self.log("PnL update thread started")

    def get_total_pnl(self):
        """Get real-time P&L directly from the PnL object"""
        try:
            if self.pnl_object and self.pnl_object.dailyPnL is not None:
                return float(self.pnl_object.dailyPnL)
            else:
                return self.current_pnl  # Fallback to stored value
        except Exception as e:
            return self.current_pnl  # Fallback to stored value

    def check_portfolio_limits(self):
        try:    
            # Get fresh P&L directly from the PnL object
            total_pnl = self.get_total_pnl()
            
            # Check for $500 loss limit
            if total_pnl <= -500:
                self.log(f"Portfolio loss limit reached: ${round(total_pnl, 2)}")
                self.close_all_positions("Portfolio loss limit of $500 reached.")
                return True
            
            # Check for $1000 profit limit
            if total_pnl >= 1000:
                self.log(f"Portfolio profit target reached: ${round(total_pnl, 2)}")
                self.close_all_positions("Portfolio profit target of $1000 reached.")
                return True
                
            # Log current P&L only when checking limits
            if total_pnl != 0:
                self.log(f"Current Portfolio P&L: ${round(total_pnl, 2)}")
                
            return False
            
        except Exception as e:
            self.log(f"Error checking portfolio limits: {e}")
            return False

    def check_end_of_day_close(self):
        """Check if it's time for end-of-day position closure (3:30 PM EST)"""
        try:
            current_time = datetime.datetime.now().time()
            close_time = datetime.time(15,30)  # 3:30 PM EST
            
            if current_time >= close_time:
                self.log("End of day close time reached (3:30 PM EST)")
                self.close_all_positions("End of day position closure at 3:30 PM EST.")
                return True
                
            return False
            
        except Exception as e:
            self.log(f"Error checking end of day close: {e}")
            return False

    def run_trading_logic(self):
        """Run trading logic with proper event loop setup"""
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
            
            # Set up PnL subscription after connection
            if self.setup_pnl_subscription():
                # Start PnL updates
                self.start_pnl_updates()
            
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
                    self.executed_trades_count = 0  # Reset executed trades count
                    self.last_trade_day = current_date
                    self.current_pnl = 0.0  # Reset PnL for new day
                    self.log('Trade records reset for new day.')

                # Check market hours
                current_time = datetime.datetime.now().time()
                if current_time < market_open or current_time > market_close:
                    self.log('Outside trading hours.')
                    time.sleep(60)  # Check again in 1 minute
                    continue

                # Check portfolio limits (loss/profit)
                if self.check_portfolio_limits():
                    self.log("Portfolio limit reached - stopping trading for the day.")
                    break

                # Check for end-of-day closure (3:30 PM EST)
                if self.check_end_of_day_close():
                    self.log("End of day closure completed - stopping trading for the day.")
                    break

                # Check max trades limit (use executed trades count)
                if self.executed_trades_count >= max_trades_per_day:
                    self.log('Max executed trades for the day reached.')
                    time.sleep(300)  # Wait 5 minutes
                    continue

                # Reset scanning timer for this cycle
                self.scanning_start_time = time.time()

                # Process each stock
                for symbol in stock_symbols:
                    if not self.running:
                        break

                    try:
                        # Get market data
                        contract = Stock(symbol, 'SMART', 'USD')
                        self.ib.qualifyContracts(contract)

                        # Get historical data (synchronous)
                        bars = self.ib.reqHistoricalData(
                            contract,
                            '',
                            '3600 S',  
                            '5 mins',  # 5-minute bars
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

                        if self.running and signal in ('BUY', 'SELL'):
                            # NEW FEATURE: Check for existing position before placing order
                            has_position, position_size, position_type = self.check_existing_position(symbol)
                            
                            if has_position:
                                # Position already exists - log message and skip order
                                self.log(f" SIGNAL IGNORED for {symbol} ({signal}) - Active {position_type} position exists (Size: {position_size})")
                                continue  # Skip to next symbol
                            
                            # No existing position - proceed with order placement
                            self.log(f"No existing position for {symbol} - proceeding with {signal} order")
                            
                            sl_amount = float(self.sl_amt_entry.get())  # $1 default
                            tp_amount = float(self.profit_amt_entry.get())  # $2 default
                            qty = 100
                            current_price = float(bars[-1].close)

                            if signal == 'BUY':
                                sl_price = current_price - sl_amount
                                tp_price = current_price + tp_amount

                                # Create bracket order
                                parent_order = MarketOrder('BUY', qty)
                                stop_loss_order = StopOrder('SELL', qty, sl_price)
                                take_profit_order = LimitOrder('SELL', qty, tp_price)

                            else:  # SELL signal
                                sl_price = current_price + sl_amount
                                tp_price = current_price - tp_amount

                                # Create bracket order
                                parent_order = MarketOrder('SELL', qty)
                                stop_loss_order = StopOrder('BUY', qty, sl_price)
                                take_profit_order = LimitOrder('BUY', qty, tp_price)

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
                                
                                # Only increment executed trades count after successful order placement
                                self.executed_trades_count += 1

                                # Record trade
                                self.trades_today.append({
                                    'symbol': symbol,
                                    'action': signal,
                                    'entry': current_price,
                                    'stop_loss': round(sl_price, 2),
                                    'take_profit': round(tp_price, 2),
                                    'qty': qty,
                                    'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                })

                                self.log(f"{signal} order EXECUTED for {symbol} at {current_price} | SL: {round(sl_price, 2)} | TP: {round(tp_price, 2)}")
                                
                            except Exception as order_error:
                                self.log(f"Failed to execute {signal} order for {symbol}: {order_error}")

                    except Exception as e:
                        self.log(f"Error processing {symbol}: {e}")
                        continue

                # Update trade statistics
                self.update_trade_statistics()

                # Export trades to CSV
                self.export_trades()

                # Wait before next cycle (5 minutes) with real-time scanning message
                if self.running:
                    self.log("BOT is scanning for trades...")
                    time.sleep(300)

            except Exception as e:
                self.log(f"Main loop error: {e}")
                time.sleep(60)  # Wait 1 minute before retrying

        # Disconnect when stopped
        if self.ib and self.ib.isConnected():
            # Cancel PnL subscription before disconnecting
            if self.pnl_object:
                try:
                    account = self.ib.managedAccounts()[0]
                    self.ib.cancelPnL(account, "")
                    self.pnl_object = None
                    self.log("PnL subscription cancelled.")
                except Exception as e:
                    self.log(f"Error cancelling PnL subscription: {e}")
            
            self.ib.disconnect()
            self.log('Disconnected from IB')
        
        # Close the event loop
        try:
            loop.close()
        except Exception as e:
            self.log(f'Error closing event loop: {e}')

    def update_trade_statistics(self):
        """Update trade statistics using executed trades count and current PnL"""
        try:
            pnl_text = f"${self.current_pnl:.2f}" if self.current_pnl != 0 else "$0.00"
            self.status_label.config(text=f"Executed Trades: {self.executed_trades_count} | Daily P&L: {pnl_text}")
        except Exception as e:
            self.log(f"Error updating statistics: {e}")

    def export_trades(self):
        try:
            if not self.trades_today:
                return

            file_exists = os.path.isfile(self.export_file)

            with open(self.export_file, 'a') as f:  # append mode
                if not file_exists:
                    # Write header only once
                    f.write('symbol,action,entry,stop_loss,take_profit,qty,timestamp\n')

                for trade in self.trades_today:
                    f.write(f"{trade['symbol']},{trade['action']},{trade['entry']},"
                            f"{trade['stop_loss']},{trade['take_profit']},"
                            f"{trade['qty']},{trade['timestamp']}\n")

            self.trades_today.clear()

            self.log(f"Trades appended to {self.export_file}")

        except Exception as e:
            self.log(f"Export error: {e}")

if __name__ == '__main__':
    root = tk.Tk()
    app = TradingBotGUI(root)
    root.mainloop()