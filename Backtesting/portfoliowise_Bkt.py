from datetime import date, timedelta
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from databento import Historical
import os
import warnings
import time
import seaborn as sns
from collections import defaultdict
warnings.filterwarnings('ignore')

class CandleData:
    def __init__(self, open_price, high, low, close, volume):
        self.open = open_price
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

def detect_engulfing_signal(candles):
    """
    Original strategy function for detecting engulfing patterns
    """
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

    if c7 <= c6 and (c6 <= c1 or c6 <= c3 or c6 <= c5) and c5 >= c4 and v7 >= v6 * 0.85 and v6 >= v5 * 0.8:
        return 'SELL'

    return None

class PortfolioTrade:
    def __init__(self, entry_time, entry_price, signal_type, stop_loss, take_profit, quantity, symbol, trade_id):
        self.trade_id = trade_id
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.signal_type = signal_type
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.quantity = quantity
        self.symbol = symbol
        self.exit_time = None
        self.exit_price = None
        self.exit_reason = None
        self.pnl_per_share = 0
        self.pnl_pct = 0
        self.gross_pnl = 0
        self.capital_used = quantity * entry_price
        self.is_active = True

class PortfolioBacktester:
    def __init__(self, symbols, api_key, start_date=None, end_date=None, sl_fixed=1.0, tp_fixed=2.0, 
                 initial_capital=100000, fixed_quantity=100):
        """
        Portfolio backtester that runs all tickers together with shared capital
        
        Args:
            symbols: List of ticker symbols
            api_key: Databento API key
            start_date: Start date (default: 1 year ago)
            end_date: End date (default: yesterday)
            sl_fixed: Fixed stop loss in dollars
            tp_fixed: Fixed take profit in dollars
            initial_capital: Total starting capital (default: $100,000)
            fixed_quantity: Fixed number of shares per trade (default: 100)
        """
        self.symbols = symbols
        self.api_key = api_key
        self.start_date = start_date or (date.today() - timedelta(days=365))
        self.end_date = end_date or (date.today() - timedelta(days=1))
        self.sl_fixed = sl_fixed
        self.tp_fixed = tp_fixed
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.fixed_quantity = fixed_quantity
        
        # Portfolio state
        self.symbol_data = {}
        self.active_trades = []
        self.closed_trades = []
        self.equity_curve = []
        self.equity_dates = []
        self.trade_counter = 0
        
        # Tracking structures
        self.symbol_indices = {}  # Track current index for each symbol
        self.unified_timeline = []
        
        # Results directory
        self.results_dir = f"portfolio_backtest_{self.start_date}_{self.end_date}"
        os.makedirs(self.results_dir, exist_ok=True)
        os.makedirs(f"{self.results_dir}/charts", exist_ok=True)
    
    def download_all_data(self):
        """Download and process data for all symbols"""
        print(f"Downloading 1-year data for {len(self.symbols)} symbols...")
        
        client = Historical(self.api_key)
        successful_downloads = []
        
        for symbol in self.symbols:
            try:
                print(f"Downloading {symbol}...")
                
                store = client.timeseries.get_range(
                    dataset="XNAS.ITCH",
                    symbols=symbol,
                    schema="ohlcv-1m",
                    start=self.start_date.isoformat(),
                    end=self.end_date.isoformat(),
                )
                
                df = store.to_df()
                
                if df.empty:
                    print(f"No data for {symbol}")
                    continue
                
                # Process data to 5-minute candles
                df.index = pd.to_datetime(df.index, utc=True)
                df = df.tz_convert("America/New_York")
                df = df.between_time("09:30", "16:00")
                df.index = df.index.tz_localize(None)
                
                df_5min = df.resample("5T").agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum"
                }).dropna()
                
                df_5min.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
                
                if len(df_5min) >= 50:
                    self.symbol_data[symbol] = df_5min
                    self.symbol_indices[symbol] = 7  # Start from index 7 for pattern detection
                    successful_downloads.append(symbol)
                    print(f"✓ {symbol}: {len(df_5min)} candles")
                else:
                    print(f"✗ {symbol}: Insufficient data")
                
                time.sleep(0.3)  # Rate limiting
                
            except Exception as e:
                print(f"✗ {symbol}: Error - {e}")
        
        self.symbols = successful_downloads
        print(f"\nSuccessfully loaded data for {len(self.symbols)} symbols")
        
        if self.symbols:
            self.create_unified_timeline()
        
        return len(self.symbols) > 0
    
    def create_unified_timeline(self):
        """Create unified timeline from all symbol data"""
        all_times = set()
        for symbol, data in self.symbol_data.items():
            all_times.update(data.index)
        
        self.unified_timeline = sorted(list(all_times))
        print(f"Created unified timeline with {len(self.unified_timeline)} time points")
    
    def convert_to_candle_objects(self, symbol, end_idx):
        """Convert DataFrame rows to CandleData objects"""
        data = self.symbol_data[symbol]
        start_idx = max(0, end_idx - 6)
        candles = []
        
        for i in range(start_idx, end_idx + 1):
            row = data.iloc[i]
            candle = CandleData(
                open_price=row['Open'],
                high=row['High'],
                low=row['Low'],
                close=row['Close'],
                volume=row['Volume']
            )
            candles.append(candle)
        
        return candles
    
    def calculate_sl_tp(self, entry_price, signal_type):
        """Calculate stop loss and take profit levels"""
        if signal_type == 'BUY':
            stop_loss = entry_price - self.sl_fixed
            take_profit = entry_price + self.tp_fixed
        else:
            stop_loss = entry_price + self.sl_fixed
            take_profit = entry_price - self.tp_fixed
        
        return stop_loss, take_profit
    
    def check_if_can_afford_trade(self, entry_price):
        """Check if we have enough capital for the trade"""
        required_capital = self.fixed_quantity * entry_price
        return required_capital <= self.current_capital
    
    def process_exits_at_time(self, current_time):
        """Process all trade exits at current time point"""
        trades_to_close = []
        
        for trade in self.active_trades:
            if current_time in self.symbol_data[trade.symbol].index:
                current_row = self.symbol_data[trade.symbol].loc[current_time]
                
                # Check exit conditions
                if self.check_exit_conditions(trade, current_time, current_row):
                    trades_to_close.append(trade)
        
        # Close trades and update capital
        for trade in trades_to_close:
            self.close_trade(trade)
            self.active_trades.remove(trade)
    
    def check_exit_conditions(self, trade, current_time, current_data):
        """Check if trade should be closed"""
        high = current_data['High']
        low = current_data['Low']
        
        if trade.signal_type == 'BUY':
            if low <= trade.stop_loss:
                trade.exit_price = trade.stop_loss
                trade.exit_time = current_time
                trade.exit_reason = 'Stop Loss'
                return True
            elif high >= trade.take_profit:
                trade.exit_price = trade.take_profit
                trade.exit_time = current_time
                trade.exit_reason = 'Take Profit'
                return True
        else:  # SELL
            if high >= trade.stop_loss:
                trade.exit_price = trade.stop_loss
                trade.exit_time = current_time
                trade.exit_reason = 'Stop Loss'
                return True
            elif low <= trade.take_profit:
                trade.exit_price = trade.take_profit
                trade.exit_time = current_time
                trade.exit_reason = 'Take Profit'
                return True
        
        return False
    
    def close_trade(self, trade):
        """Close a trade and calculate P&L"""
        trade.is_active = False
        
        if trade.signal_type == 'BUY':
            trade.pnl_per_share = trade.exit_price - trade.entry_price
        else:
            trade.pnl_per_share = trade.entry_price - trade.exit_price
        
        trade.pnl_pct = (trade.pnl_per_share / trade.entry_price) * 100
        trade.gross_pnl = trade.pnl_per_share * trade.quantity
        
        # Return capital to available pool
        self.current_capital += trade.capital_used + trade.gross_pnl
        self.closed_trades.append(trade)
        
        print(f"Closed {trade.symbol} {trade.signal_type}: P&L ${trade.gross_pnl:+.2f} | Available: ${self.current_capital:,.2f}")
    
    def process_signals_at_time(self, current_time):
        """Process all new signals at current time point"""
        signals_found = []
        
        # Check each symbol for signals
        for symbol in self.symbols:
            # Skip if we already have a position in this symbol
            if any(trade.symbol == symbol for trade in self.active_trades):
                continue
            
            if current_time not in self.symbol_data[symbol].index:
                continue
            
            # Get current index for this symbol
            symbol_timeline = list(self.symbol_data[symbol].index)
            current_idx = symbol_timeline.index(current_time)
            
            if current_idx >= 7:  # Need at least 7 candles
                candles = self.convert_to_candle_objects(symbol, current_idx)
                signal = detect_engulfing_signal(candles)
                
                if signal:
                    entry_price = self.symbol_data[symbol].loc[current_time, 'Close']
                    signals_found.append((symbol, signal, entry_price))
        
        # Process all valid signals
        for symbol, signal, entry_price in signals_found:
            if self.check_if_can_afford_trade(entry_price):
                self.open_trade(symbol, signal, entry_price, current_time)
    
    def open_trade(self, symbol, signal, entry_price, entry_time):
        """Open a new trade"""
        self.trade_counter += 1
        stop_loss, take_profit = self.calculate_sl_tp(entry_price, signal)
        
        new_trade = PortfolioTrade(
            entry_time=entry_time,
            entry_price=entry_price,
            signal_type=signal,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=self.fixed_quantity,
            symbol=symbol,
            trade_id=self.trade_counter
        )
        
        # Deduct capital
        self.current_capital -= new_trade.capital_used
        self.active_trades.append(new_trade)
        
        print(f"Opened {symbol} {signal} @ ${entry_price:.2f} | Used: ${new_trade.capital_used:,.2f} | Available: ${self.current_capital:,.2f}")
    
    def calculate_current_portfolio_value(self, current_time):
        """Calculate total portfolio value including unrealized P&L"""
        total_value = self.current_capital
        
        for trade in self.active_trades:
            if current_time in self.symbol_data[trade.symbol].index:
                current_price = self.symbol_data[trade.symbol].loc[current_time, 'Close']
                
                if trade.signal_type == 'BUY':
                    unrealized_pnl = (current_price - trade.entry_price) * trade.quantity
                else:
                    unrealized_pnl = (trade.entry_price - current_price) * trade.quantity
                
                total_value += trade.capital_used + unrealized_pnl
            else:
                # Use last known value
                total_value += trade.capital_used
        
        return total_value
    
    def run_portfolio_backtest(self):
        """Run the unified portfolio backtest"""
        if not self.download_all_data():
            print("Failed to download data")
            return False
        
        print(f"\n{'='*80}")
        print("RUNNING UNIFIED PORTFOLIO BACKTEST")
        print(f"{'='*80}")
        print(f"Initial Capital: ${self.initial_capital:,}")
        print(f"Fixed Quantity per Trade: {self.fixed_quantity} shares")
        print(f"Stop Loss: ${self.sl_fixed} | Take Profit: ${self.tp_fixed}")
        print(f"Processing {len(self.unified_timeline)} time points...")
        
        # Initialize equity curve
        self.equity_curve = [self.initial_capital]
        self.equity_dates = [self.unified_timeline[0]]
        
        # Process each time point in chronological order
        for i, current_time in enumerate(self.unified_timeline):
            if i % 1000 == 0:
                print(f"Progress: {i}/{len(self.unified_timeline)} ({i/len(self.unified_timeline)*100:.1f}%)")
            
            # Step 1: Process exits first
            self.process_exits_at_time(current_time)
            
            # Step 2: Process new signals
            self.process_signals_at_time(current_time)
            
            # Step 3: Update equity curve
            portfolio_value = self.calculate_current_portfolio_value(current_time)
            self.equity_curve.append(portfolio_value)
            self.equity_dates.append(current_time)
        
        # Close any remaining open trades
        for trade in self.active_trades:
            last_data = self.symbol_data[trade.symbol].iloc[-1]
            trade.exit_price = last_data['Close']
            trade.exit_time = self.symbol_data[trade.symbol].index[-1]
            trade.exit_reason = 'Market Close'
            self.close_trade(trade)
        
        self.active_trades = []
        
        print(f"\nBacktest Completed!")
        print(f"Final Portfolio Value: ${self.current_capital:,.2f}")
        print(f"Total Return: {((self.current_capital / self.initial_capital) - 1) * 100:+.2f}%")
        print(f"Total Trades Executed: {len(self.closed_trades)}")
        
        return True
    
    def calculate_portfolio_metrics(self):
        """Calculate comprehensive portfolio metrics"""
        if not self.closed_trades:
            return {}
        
        total_trades = len(self.closed_trades)
        winning_trades = [t for t in self.closed_trades if t.gross_pnl > 0]
        losing_trades = [t for t in self.closed_trades if t.gross_pnl < 0]
        
        win_rate = (len(winning_trades) / total_trades) * 100
        total_pnl = sum(t.gross_pnl for t in self.closed_trades)
        total_return_pct = (total_pnl / self.initial_capital) * 100
        
        avg_win = np.mean([t.gross_pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t.gross_pnl for t in losing_trades]) if losing_trades else 0
        
        profit_factor = abs(sum(t.gross_pnl for t in winning_trades) / sum(t.gross_pnl for t in losing_trades)) if losing_trades else float('inf')
        
        # Drawdown calculation
        peak = self.initial_capital
        max_drawdown = 0
        max_drawdown_pct = 0
        
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            drawdown_pct = (drawdown / peak) * 100 if peak > 0 else 0
            
            max_drawdown = max(max_drawdown, drawdown)
            max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
        
        # Sharpe ratio calculation
        returns = np.diff(self.equity_curve) / self.equity_curve[:-1]
        avg_return = np.mean(returns) if len(returns) > 0 else 0
        std_return = np.std(returns, ddof=1) if len(returns) > 1 else 0
        sharpe_ratio = (avg_return / std_return) * np.sqrt(252 * 78) if std_return > 0 else 0
        
        return {
            'initial_capital': self.initial_capital,
            'final_capital': self.current_capital,
            'total_pnl': total_pnl,
            'total_return_pct': total_return_pct,
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown_pct,
            'sharpe_ratio': sharpe_ratio,
            'largest_win': max([t.gross_pnl for t in self.closed_trades]),
            'largest_loss': min([t.gross_pnl for t in self.closed_trades]),
            'avg_trade_pnl': total_pnl / total_trades,
            'symbols_traded': len(set(t.symbol for t in self.closed_trades))
        }
    
    def get_ticker_breakdown(self):
        """Get detailed breakdown by ticker"""
        ticker_stats = defaultdict(lambda: {
            'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
            'total_pnl': 0, 'win_rate': 0, 'avg_pnl_per_trade': 0,
            'largest_win': 0, 'largest_loss': 0, 'first_trade': None, 'last_trade': None
        })
        
        for trade in self.closed_trades:
            symbol = trade.symbol
            stats = ticker_stats[symbol]
            
            stats['total_trades'] += 1
            stats['total_pnl'] += trade.gross_pnl
            
            if trade.gross_pnl > 0:
                stats['winning_trades'] += 1
                stats['largest_win'] = max(stats['largest_win'], trade.gross_pnl)
            else:
                stats['losing_trades'] += 1
                stats['largest_loss'] = min(stats['largest_loss'], trade.gross_pnl)
            
            if stats['first_trade'] is None or trade.entry_time < stats['first_trade']:
                stats['first_trade'] = trade.entry_time
            if stats['last_trade'] is None or trade.entry_time > stats['last_trade']:
                stats['last_trade'] = trade.entry_time
        
        # Calculate derived metrics
        for symbol, stats in ticker_stats.items():
            if stats['total_trades'] > 0:
                stats['win_rate'] = (stats['winning_trades'] / stats['total_trades']) * 100
                stats['avg_pnl_per_trade'] = stats['total_pnl'] / stats['total_trades']
        
        # Include symbols with no trades
        for symbol in self.symbols:
            if symbol not in ticker_stats:
                ticker_stats[symbol] = {
                    'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                    'total_pnl': 0, 'win_rate': 0, 'avg_pnl_per_trade': 0,
                    'largest_win': 0, 'largest_loss': 0, 'first_trade': None, 'last_trade': None
                }
        
        return dict(ticker_stats)
    
    def export_all_csvs(self):
        """Export all analysis to CSV files"""
        print("\nExporting CSV files...")
        
        # 1. Complete Trade Log
        trade_data = []
        for trade in self.closed_trades:
            trade_data.append({
                'Trade_ID': trade.trade_id,
                'Symbol': trade.symbol,
                'Entry_Time': trade.entry_time,
                'Exit_Time': trade.exit_time,
                'Signal_Type': trade.signal_type,
                'Entry_Price': trade.entry_price,
                'Exit_Price': trade.exit_price,
                'Stop_Loss': trade.stop_loss,
                'Take_Profit': trade.take_profit,
                'Quantity': trade.quantity,
                'Capital_Used': trade.capital_used,
                'PnL_Per_Share': trade.pnl_per_share,
                'PnL_Percentage': trade.pnl_pct,
                'Gross_PnL': trade.gross_pnl,
                'Exit_Reason': trade.exit_reason,
                'Duration_Minutes': (trade.exit_time - trade.entry_time).total_seconds() / 60,
            })
        
        df_trades = pd.DataFrame(trade_data)
        df_trades['Cumulative_PnL'] = df_trades['Gross_PnL'].cumsum()
        df_trades['Running_Capital'] = self.initial_capital + df_trades['Cumulative_PnL']
        df_trades.to_csv(f"{self.results_dir}/complete_trade_log.csv", index=False)
        
        # 2. Portfolio Performance Metrics
        metrics = self.calculate_portfolio_metrics()
        perf_data = []
        for key, value in metrics.items():
            perf_data.append({'Metric': key.replace('_', ' ').title(), 'Value': value})
        
        df_performance = pd.DataFrame(perf_data)
        df_performance.to_csv(f"{self.results_dir}/portfolio_metrics.csv", index=False)
        
        # 3. Ticker-wise Performance Breakdown
        ticker_breakdown = self.get_ticker_breakdown()
        ticker_data = []
        for symbol, stats in ticker_breakdown.items():
            stats_copy = stats.copy()
            stats_copy['Symbol'] = symbol
            ticker_data.append(stats_copy)
        
        df_tickers = pd.DataFrame(ticker_data)
        df_tickers = df_tickers.sort_values('total_pnl', ascending=False)
        
        # Calculate contribution percentages
        total_portfolio_pnl = sum(stats['total_pnl'] for stats in ticker_breakdown.values())
        if total_portfolio_pnl != 0:
            df_tickers['contribution_pct'] = (df_tickers['total_pnl'] / total_portfolio_pnl) * 100
        else:
            df_tickers['contribution_pct'] = 0
        
        df_tickers.to_csv(f"{self.results_dir}/ticker_breakdown.csv", index=False)
        
        # 4. Equity Curve Data
        equity_data = []
        for i, (timestamp, equity) in enumerate(zip(self.equity_dates, self.equity_curve)):
            # Calculate drawdown
            peak = max(self.equity_curve[:i+1])
            drawdown = peak - equity
            drawdown_pct = (drawdown / peak) * 100 if peak > 0 else 0
            
            equity_data.append({
                'Timestamp': timestamp,
                'Portfolio_Value': equity,
                'Return_Pct': ((equity / self.initial_capital) - 1) * 100,
                'Drawdown_Dollars': drawdown,
                'Drawdown_Pct': drawdown_pct,
                'Active_Positions': len([t for t in self.closed_trades if t.entry_time <= timestamp and (t.exit_time is None or t.exit_time >= timestamp)])
            })
        
        df_equity = pd.DataFrame(equity_data)
        df_equity.to_csv(f"{self.results_dir}/portfolio_equity_curve.csv", index=False)
        
        # 5. Signal Analysis
        signal_analysis = defaultdict(lambda: {'BUY': 0, 'SELL': 0, 'BUY_PnL': 0, 'SELL_PnL': 0})
        
        for trade in self.closed_trades:
            signal_analysis[trade.symbol][trade.signal_type] += 1
            signal_analysis[trade.symbol][f'{trade.signal_type}_PnL'] += trade.gross_pnl
        
        signal_data = []
        for symbol in self.symbols:
            signal_data.append({
                'Symbol': symbol,
                'BUY_Signals': signal_analysis[symbol]['BUY'],
                'SELL_Signals': signal_analysis[symbol]['SELL'],
                'Total_Signals': signal_analysis[symbol]['BUY'] + signal_analysis[symbol]['SELL'],
                'BUY_PnL': signal_analysis[symbol]['BUY_PnL'],
                'SELL_PnL': signal_analysis[symbol]['SELL_PnL'],
                'Net_PnL': signal_analysis[symbol]['BUY_PnL'] + signal_analysis[symbol]['SELL_PnL']
            })
        
        df_signals = pd.DataFrame(signal_data)
        df_signals.to_csv(f"{self.results_dir}/signal_analysis.csv", index=False)
        
        print(f"All CSV files exported to: {self.results_dir}/")
    
    def create_comprehensive_charts(self):
        """Create comprehensive visualization charts"""
        print("Generating comprehensive charts...")
        
        plt.style.use('default')
        sns.set_palette("husl")
        
        # Chart 1: Portfolio Equity Curve with Trade Markers
        plt.figure(figsize=(18, 10))
        
        # Main equity curve
        plt.plot(self.equity_dates, self.equity_curve, color='navy', linewidth=2, label='Portfolio Value')
        plt.axhline(y=self.initial_capital, color='black', linestyle='--', alpha=0.7, label='Initial Capital')
        
        # Mark trades
        for trade in self.closed_trades:
            color = 'green' if trade.gross_pnl > 0 else 'red'
            marker = '^' if trade.signal_type == 'BUY' else 'v'
            plt.scatter(trade.entry_time, 
                       [eq for eq, dt in zip(self.equity_curve, self.equity_dates) if dt == trade.entry_time][0],
                       color=color, marker=marker, s=30, alpha=0.6)
        
        plt.title('Unified Portfolio Performance with Trade Signals', fontsize=16)
        plt.ylabel('Portfolio Value ($)', fontsize=12)
        plt.xlabel('Date', fontsize=12)
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(f"{self.results_dir}/charts/portfolio_equity_curve.png", dpi=300, bbox_inches='tight')
        plt.close()
        
        # Chart 2: Ticker Performance Dashboard
        ticker_breakdown = self.get_ticker_breakdown()
        symbols = list(ticker_breakdown.keys())
        pnls = [ticker_breakdown[s]['total_pnl'] for s in symbols]
        trade_counts = [ticker_breakdown[s]['total_trades'] for s in symbols]
        win_rates = [ticker_breakdown[s]['win_rate'] for s in symbols]
        
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(20, 16))
        
        # P&L by Ticker
        colors = ['darkgreen' if pnl > 0 else 'darkred' for pnl in pnls]
        bars1 = ax1.bar(symbols, pnls, color=colors, alpha=0.7)
        ax1.axhline(y=0, color='black', linestyle='-', alpha=0.8)
        ax1.set_title('Total P&L by Ticker', fontsize=14, fontweight='bold')
        ax1.set_ylabel('P&L ($)', fontsize=12)
        ax1.tick_params(axis='x', rotation=45)
        ax1.grid(True, alpha=0.3, axis='y')
        
        # Add P&L labels
        for bar, pnl in zip(bars1, pnls):
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + (50 if height > 0 else -50),
                    f'${pnl:,.0f}', ha='center', va='bottom' if height > 0 else 'top', fontsize=9)
        
        # Trade Count by Ticker
        bars2 = ax2.bar(symbols, trade_counts, color='steelblue', alpha=0.7)
        ax2.set_title('Number of Trades by Ticker', fontsize=14, fontweight='bold')
        ax2.set_ylabel('Trade Count', fontsize=12)
        ax2.tick_params(axis='x', rotation=45)
        ax2.grid(True, alpha=0.3, axis='y')
        
        for bar, count in zip(bars2, trade_counts):
            if count > 0:
                ax2.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.1,
                        str(count), ha='center', va='bottom', fontsize=9)
        
        # Win Rate by Ticker
        colors_wr = ['green' if wr >= 50 else 'orange' if wr >= 40 else 'red' for wr in win_rates]
        bars3 = ax3.bar(symbols, win_rates, color=colors_wr, alpha=0.7)
        ax3.axhline(y=50, color='black', linestyle='--', alpha=0.7, label='50% Breakeven')
        breakeven_wr = self.sl_fixed / (self.sl_fixed + self.tp_fixed) * 100
        ax3.axhline(y=breakeven_wr, color='red', linestyle='--', alpha=0.7, 
                   label=f'Strategy Breakeven ({breakeven_wr:.1f}%)')
        ax3.set_title('Win Rate by Ticker', fontsize=14, fontweight='bold')
        ax3.set_ylabel('Win Rate (%)', fontsize=12)
        ax3.tick_params(axis='x', rotation=45)
        ax3.legend()
        ax3.grid(True, alpha=0.3, axis='y')
        
        # Contribution to Portfolio
        contribution_pcts = [abs(pnl) / sum(abs(p) for p in pnls) * 100 if sum(abs(p) for p in pnls) != 0 else 0 for pnl in pnls]
        ax4.pie(contribution_pcts, labels=symbols, autopct='%1.1f%%', startangle=90)
        ax4.set_title('Contribution to Total P&L (by absolute value)', fontsize=14, fontweight='bold')
        
        plt.tight_layout()
        plt.savefig(f"{self.results_dir}/charts/ticker_performance_dashboard.png", dpi=300, bbox_inches='tight')
        plt.close()
        
        # Chart 3: Risk Analysis
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(18, 14))
        
        # Drawdown curve
        peak_values = []
        drawdown_values = []
        peak = self.initial_capital
        
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            peak_values.append(peak)
            drawdown_values.append(((peak - equity) / peak) * 100)
        
        ax1.fill_between(self.equity_dates, 0, drawdown_values, color='red', alpha=0.3)
        ax1.plot(self.equity_dates, drawdown_values, color='red', linewidth=2)
        ax1.set_title('Portfolio Drawdown Over Time', fontweight='bold')
        ax1.set_ylabel('Drawdown (%)')
        ax1.set_xlabel('Date')
        ax1.invert_yaxis()
        ax1.grid(True, alpha=0.3)
        
        # P&L Distribution
        pnl_values = [trade.gross_pnl for trade in self.closed_trades]
        ax2.hist(pnl_values, bins=20, alpha=0.7, edgecolor='black', color='skyblue')
        ax2.axvline(x=0, color='red', linestyle='--', alpha=0.7, label='Breakeven')
        ax2.axvline(x=np.mean(pnl_values), color='green', linestyle='--', alpha=0.7, 
                   label=f'Mean (${np.mean(pnl_values):.2f})')
        ax2.set_title('Trade P&L Distribution', fontweight='bold')
        ax2.set_xlabel('P&L per Trade ($)')
        ax2.set_ylabel('Frequency')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # Monthly Returns
        df_equity = pd.DataFrame({'date': self.equity_dates, 'equity': self.equity_curve})
        df_equity['date'] = pd.to_datetime(df_equity['date'])
        df_equity.set_index('date', inplace=True)
        
        try:
            monthly_equity = df_equity['equity'].resample('M').last()
            monthly_returns = monthly_equity.pct_change().dropna() * 100
            
            colors = ['green' if r > 0 else 'red' for r in monthly_returns.values]
            bars = ax3.bar(range(len(monthly_returns)), monthly_returns.values, color=colors, alpha=0.7)
            ax3.set_title('Monthly Returns', fontweight='bold')
            ax3.set_ylabel('Return (%)')
            ax3.set_xlabel('Month')
            ax3.axhline(y=0, color='black', linestyle='-', alpha=0.5)
            ax3.set_xticks(range(len(monthly_returns)))
            ax3.set_xticklabels([dt.strftime('%b %Y') for dt in monthly_returns.index], rotation=45)
            ax3.grid(True, alpha=0.3, axis='y')
        except:
            ax3.text(0.5, 0.5, 'Insufficient data for monthly analysis', 
                    transform=ax3.transAxes, ha='center', va='center')
            ax3.set_title('Monthly Returns (Insufficient Data)', fontweight='bold')
        
        # Trade Timeline
        trade_times = [trade.entry_time for trade in self.closed_trades]
        trade_pnls = [trade.gross_pnl for trade in self.closed_trades]
        
        colors = ['green' if pnl > 0 else 'red' for pnl in trade_pnls]
        ax4.scatter(trade_times, trade_pnls, c=colors, alpha=0.6, s=50)
        ax4.axhline(y=0, color='black', linestyle='--', alpha=0.7)
        ax4.set_title('Trade P&L Timeline', fontweight='bold')
        ax4.set_ylabel('P&L per Trade ($)')
        ax4.set_xlabel('Date')
        ax4.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(f"{self.results_dir}/charts/risk_and_timeline_analysis.png", dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"Charts saved to: {self.results_dir}/charts/")
    
    def print_comprehensive_report(self):
        """Print detailed portfolio analysis report"""
        print("\n" + "="*120)
        print("UNIFIED PORTFOLIO BACKTEST - COMPREHENSIVE RESULTS")
        print("="*120)
        
        # Portfolio Summary
        metrics = self.calculate_portfolio_metrics()
        print(f"\nPORTFOLIO SUMMARY:")
        print(f"Initial Capital: ${metrics['initial_capital']:,}")
        print(f"Final Capital: ${metrics['final_capital']:,.2f}")
        print(f"Total Return: {metrics['total_return_pct']:+.2f}%")
        print(f"Total P&L: ${metrics['total_pnl']:+,.2f}")
        
        print(f"\nTRADING STATISTICS:")
        print(f"Total Trades: {metrics['total_trades']}")
        print(f"Winning Trades: {metrics['winning_trades']} ({metrics['win_rate']:.1f}%)")
        print(f"Losing Trades: {metrics['losing_trades']}")
        print(f"Average Win: ${metrics['avg_win']:,.2f}")
        print(f"Average Loss: ${metrics['avg_loss']:,.2f}")
        print(f"Profit Factor: {metrics['profit_factor']:.2f}")
        print(f"Largest Win: ${metrics['largest_win']:,.2f}")
        print(f"Largest Loss: ${metrics['largest_loss']:,.2f}")
        
        print(f"\nRISK METRICS:")
        print(f"Maximum Drawdown: ${metrics['max_drawdown']:,.2f} ({metrics['max_drawdown_pct']:.2f}%)")
        print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
        print(f"Symbols Actively Traded: {metrics['symbols_traded']}")
        
        # Ticker Breakdown
        ticker_breakdown = self.get_ticker_breakdown()
        print(f"\nTICKER-WISE PERFORMANCE:")
        print("-" * 100)
        print(f"{'Symbol':<8} {'Trades':<8} {'Wins':<6} {'Win%':<8} {'Total P&L':<12} {'Avg P&L':<12} {'Best':<12} {'Worst':<12}")
        print("-" * 100)
        
        # Sort by total P&L
        sorted_tickers = sorted(ticker_breakdown.items(), key=lambda x: x[1]['total_pnl'], reverse=True)
        
        for symbol, stats in sorted_tickers:
            if stats['total_trades'] > 0:
                print(f"{symbol:<8} {stats['total_trades']:<8} {stats['winning_trades']:<6} "
                      f"{stats['win_rate']:<8.1f} ${stats['total_pnl']:<11,.2f} "
                      f"${stats['avg_pnl_per_trade']:<11,.2f} ${stats['largest_win']:<11,.2f} "
                      f"${stats['largest_loss']:<11,.2f}")
            else:
                print(f"{symbol:<8} {'0':<8} {'0':<6} {'0.0':<8} {'$0.00':<12} {'$0.00':<12} {'$0.00':<12} {'$0.00':<12}")
        
        print("-" * 100)
        
        # Top Contributors
        profitable_tickers = [(symbol, stats['total_pnl']) for symbol, stats in sorted_tickers if stats['total_pnl'] > 0]
        unprofitable_tickers = [(symbol, stats['total_pnl']) for symbol, stats in sorted_tickers if stats['total_pnl'] < 0]
        
        if profitable_tickers:
            print(f"\nTOP CONTRIBUTORS:")
            for symbol, pnl in profitable_tickers[:5]:
                print(f"  {symbol}: ${pnl:+,.2f}")
        
        if unprofitable_tickers:
            print(f"\nWORST PERFORMERS:")
            for symbol, pnl in unprofitable_tickers[-3:]:
                print(f"  {symbol}: ${pnl:+,.2f}")
        
        print(f"\nEXPORTED FILES:")
        print(f"📁 {self.results_dir}/")
        print(f"  📊 complete_trade_log.csv - All trade details")
        print(f"  📈 portfolio_metrics.csv - Performance metrics")  
        print(f"  🏢 ticker_breakdown.csv - Per-ticker analysis")
        print(f"  📉 portfolio_equity_curve.csv - Portfolio value timeline")
        print(f"  🎯 signal_analysis.csv - Signal effectiveness")
        print(f"  📊 charts/ - All visualization diagrams")
        
        print("="*120)


# Main execution function
def main_portfolio_backtest():
    """Main execution for portfolio-style backtest"""
    
    # Configuration
    API_KEY = "db-V6dFxJLQ3LHWH9HNKGYqk7TDCf7tn"
    
    # Symbols to analyze
    SYMBOLS = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META',
        'TSLA', 'NVDA', 'AMD', 'CRM', 'AVGO',
        'JPM', 'HD', 'CAT', 'URI', 'NFLX'
    ]
    
    # Date range: 1 year as requested
    END_DATE = date.today() - timedelta(days=1)
    START_DATE = END_DATE - timedelta(days=365)
    
    # Strategy parameters
    STOP_LOSS = 1.0           # $1 fixed stop loss
    TAKE_PROFIT = 2.0         # $2 fixed take profit  
    INITIAL_CAPITAL = 100000  # $100K total capital
    FIXED_QUANTITY = 100      # 100 shares per trade
    
    print("PORTFOLIO ENGULFING STRATEGY BACKTEST")
    print("=" * 80)
    print(f"🎯 Strategy: Engulfing Pattern Detection")
    print(f"💰 Initial Capital: ${INITIAL_CAPITAL:,}")
    print(f"📅 Period: {START_DATE} to {END_DATE} (1 Year)")
    print(f"📈 Symbols: {len(SYMBOLS)} tickers")
    print(f"🔄 Fixed Quantity: {FIXED_QUANTITY} shares per trade")
    print(f"🛑 Stop Loss: ${STOP_LOSS} | 🎯 Take Profit: ${TAKE_PROFIT}")
    print("=" * 80)
    
    # Initialize portfolio backtester
    backtester = PortfolioBacktester(
        symbols=SYMBOLS,
        api_key=API_KEY,
        start_date=START_DATE,
        end_date=END_DATE,
        sl_fixed=STOP_LOSS,
        tp_fixed=TAKE_PROFIT,
        initial_capital=INITIAL_CAPITAL,
        fixed_quantity=FIXED_QUANTITY
    )
    
    # Run complete analysis
    print("Starting unified portfolio backtest...")
    success = backtester.run_portfolio_backtest()
    
    if success:
        # Export all data to CSV
        backtester.export_all_csvs()
        
        # Generate comprehensive charts
        try:
            backtester.create_comprehensive_charts()
        except Exception as e:
            print(f"Warning: Visualization error: {e}")
        
        # Print detailed report
        backtester.print_comprehensive_report()
        
        return True
    else:
        print("Portfolio backtest failed.")
        return False


if __name__ == "__main__":
    main_portfolio_backtest()