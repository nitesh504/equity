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
    if c7 <= c1 and c7 <= c2 and c3 >= c3 and c7 <= c5 and v7 >= v6 * 0.85 and v7 >= v5 * 0.8:
        return 'SELL'

    return None

class TradingBotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title('Equitor')
        self.stock_list = ['SPY', 'QQQ','AAPL','TSLA','JPM','MSFT','GOOGL','NVDA','INTC']
        #self.stock_list = ['SPY', 'QQQ','AAPL']
        self.trades_today = []
        self.export_file = 'trades_export.csv'
        self.running = False
        self.last_trade_day = datetime.datetime.now().date()
        self.ib = None
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
        tk.Label(self.root, text='Profit Target %:').grid(row=3, column=0)
        self.profit_entry = tk.Entry(self.root)
        self.profit_entry.insert(0, '5')
        self.profit_entry.grid(row=3, column=1)

        tk.Label(self.root, text='Stop Loss %:').grid(row=3, column=2)
        self.sl_entry = tk.Entry(self.root)
        self.sl_entry.insert(0, '2')
        self.sl_entry.grid(row=3, column=3)

        tk.Label(self.root, text='Profit Amount:').grid(row=3, column=4)
        self.profit_amt_entry = tk.Entry(self.root)
        self.profit_amt_entry.insert(0, '2.5')
        self.profit_amt_entry.grid(row=3, column=5)

        tk.Label(self.root, text='Stop Loss Amount:').grid(row=4, column=0)
        self.sl_amt_entry = tk.Entry(self.root)
        self.sl_amt_entry.insert(0, '1.0')
        self.sl_amt_entry.grid(row=4, column=1)

        # Log area
        self.log_area = scrolledtext.ScrolledText(self.root, width=80, height=10)
        self.log_area.grid(row=5, column=0, columnspan=6, pady=10)

        # Status
        self.status_label = tk.Label(self.root, text='No trades today.')
        self.status_label.grid(row=6, column=0, columnspan=6)

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

    def start_bot(self):
        if not self.running:
            self.running = True
            self.log('Bot started.')
            threading.Thread(target=self.run_trading_logic, daemon=True).start()

    def stop_bot(self):
        self.running = False
        self.log('Bot stopped.')
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()

    def log(self, msg):
        self.log_area.insert(tk.END, f"{datetime.datetime.now().strftime('%H:%M:%S')} - {msg}\n")
        self.log_area.see(tk.END)

    def close_all_positions(self, reason=""):
        """Close all open positions"""
        try:
            positions = self.ib.positions()
            if not positions:
                self.log(f"No positions to close. {reason}")
                return True

            closed_count = 0
            for position in positions:
                if position.position != 0:  # Only cl1contract = position.contract
                    qty = abs(position.position)
                    
                    # Determine order action (opposite of current position)
                    if position.position > 0:
                        action = 'SELL'  # Close long position
                    else:
                        action = 'BUY'   # Close short position
                    
                    # Create market order to close position
                    close_order = MarketOrder(action, qty)
                    self.ib.placeOrder(contract, close_order)
                    
                    self.log(f"Closing {position.position} shares of {contract.symbol} - {action} {qty} shares")
                    closed_count += 1

            if closed_count > 0:
                self.log(f"Closed {closed_count} positions. {reason}")
                # Wait a bit for orders to fill
                time.sleep(5)
            
            return True

        except Exception as e:
            self.log(f"Error closing positions: {e}")
            return False

    def get_total_pnl(self):
        """Calculate total portfolio P&L"""
        try:
            total_pnl = 0.0
            positions = self.ib.positions()
            
            for position in positions:
                if position.position != 0:  # Only consider non-zero positions
                    # Get current market price
                    contract = position.contract
                    ticker = self.ib.reqMktData(contract, '', False, False)
                    self.ib.sleep(1)  # Give time for data to arrive
                    
                    current_price = ticker.last or ticker.close or position.avgCost
                    
                    if current_price and current_price > 0:
                        # Calculate P&L for this position
                        position_pnl = (current_price - position.avgCost) * position.position
                        total_pnl += position_pnl
                    
                    # Cancel market data subscription
                    self.ib.cancelMktData(contract)
            
            return total_pnl
            
        except Exception as e:
            self.log(f"Error calculating total P&L: {e}")
            return 0.0

    def check_portfolio_limits(self):
        """Check if portfolio has hit profit/loss limits"""
        try:
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
                
            return False
            
        except Exception as e:
            self.log(f"Error checking portfolio limits: {e}")
            return False

    def check_end_of_day_close(self):
        """Check if it's time for end-of-day position closure (3:30 PM EST)"""
        try:
            current_time = datetime.datetime.now().time()
            close_time = datetime.time(15, 30)  # 3:30 PM
            
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
        profit_target = float(self.profit_entry.get()) / 100
        stop_loss = float(self.sl_entry.get()) / 100

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
                    self.last_trade_day = current_date
                    self.log('Trade records reset for new day.')

                # Check market hours
                current_time = datetime.datetime.now().time()
                if current_time < market_open or current_time > market_close:
                    self.log('Outside trading hours.')
                    time.sleep(60)  # Check again in 1 minute
                    continue

                # NEW FEATURE: Check portfolio limits (loss/profit)
                if self.check_portfolio_limits():
                    self.log("Portfolio limit reached - stopping trading for the day.")
                    break

                # NEW FEATURE: Check for end-of-day closure (3:30 PM EST)
                if self.check_end_of_day_close():
                    self.log("End of day closure completed - stopping trading for the day.")
                    break

                # Check max trades limit
                if len(self.trades_today) >= max_trades_per_day:
                    self.log('Max trades for the day reached.')
                    time.sleep(300)  # Wait 5 minutes
                    continue

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
                            sl_amount = float(self.sl_amt_entry.get())
                            tp_amount = float(self.profit_amt_entry.get())
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

                            for order in bracket_order:
                                self.ib.placeOrder(contract, order)

                            # Record trade
                            self.trades_today.append({
                                'symbol': symbol,
                                'action': signal,
                                'entry': current_price,
                                'timestamp': datetime.datetime.now()
                            })

                            self.log(f"{signal} order placed for {symbol} at {current_price} | SL: {round(sl_price, 2)} | TP: {round(tp_price, 2)}")

                    except Exception as e:
                        self.log(f"Error processing {symbol}: {e}")
                        continue

                # Update trade statistics
                self.update_trade_statistics()

                # Export trades to CSV
                self.export_trades()

                # Wait before next cycle (5 minutes)
                if self.running:
                    time.sleep(300)

            except Exception as e:
                self.log(f"Main loop error: {e}")
                time.sleep(60)  # Wait 1 minute before retrying

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
        # bars = self.ib.reqHistoricalData
        # wins, losses, total_trades, pnl = 0, 0, len(self.trades_today), 0.0
        # profit_target = float(self.profit_entry.get()) / 100

        try:
            bars = self.ib.reqHistoricalData
            wins, losses, total_trades, pnl = 0, 0, len(self.trades_today), 0.0
            profit_target = float(self.profit_entry.get()) / 100
        # except:
        #     "ERRRORR"
        # finally:
        #     "DONE"

            for trade in self.trades_today:
                try:
                    # Get current market data
                    contract = Stock(trade['symbol'], 'SMART', 'USD')
                    ticker = self.ib.reqMktData(contract, '', False, False)
                    self.ib.sleep(1)  # Give time for data to arrive
                    
                    current_price = ticker.last or ticker.close or trade['entry']
                    
                    if current_price and current_price > 0:
                        # Calculate profit/loss
                        if trade['action'] == 'BUY':
                            trade_pnl = current_price - trade['entry']
                            pct_change = (current_price - trade['entry']) / trade['entry']
                        else:
                            trade_pnl = trade['entry'] - current_price
                            pct_change = (trade['entry'] - current_price) / trade['entry']

                        pnl += trade_pnl
                        
                        # Determine if it's a win or loss
                        if pct_change >= profit_target:
                            wins += 1
                        else:
                            losses += 1
                    
                    # Cancel market data subscription
                    self.ib.cancelMktData(contract)
                    
                except Exception as e:
                    self.log(f"Error updating stats for {trade['symbol']}: {e}")
                    continue

            # Update status label with portfolio P&L
            portfolio_pnl = self.get_total_pnl()
            self.status_label.config(text=f"Trades: {total_trades} | Wins: {wins} | Losses: {losses} | P/L: ${round(pnl, 2)} | Portfolio P/L: ${round(portfolio_pnl, 2)}")

        except Exception as e:
            self.log(f"Error updating statistics: {e}")

    def export_trades(self):
        """Export trades to CSV file"""
        try:
            if not self.trades_today:
                return

            with open(self.export_file, 'w') as f:
                f.write('symbol,action,entry,timestamp\n')
                
                for trade in self.trades_today:
                    f.write(f"{trade['symbol']},{trade['action']},{trade['entry']},{trade['timestamp']}\n")

            self.log(f"Trades exported to {self.export_file}")

        except Exception as e:
            self.log(f"Export error: {e}")

if __name__ == '__main__':
    root = tk.Tk()
    app = TradingBotGUI(root)
    root.mainloop()