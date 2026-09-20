from datetime import date, timedelta
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from databento import Historical
import os
import warnings
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import seaborn as sns
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
    Your original strategy function
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

class Trade:
    def __init__(self, entry_time, entry_price, signal_type, stop_loss, take_profit, quantity=100):
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.signal_type = signal_type
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.quantity = quantity
        self.exit_time = None
        self.exit_price = None
        self.exit_reason = None
        self.pnl = 0
        self.pnl_pct = 0
        self.gross_pnl = 0

class EngulfingBacktester:
    def __init__(self, symbol, api_key, start_date=None, end_date=None, sl_fixed=1.0, tp_fixed=2.0, initial_capital=10000, quantity=100, base_results_dir=None):
        self.symbol = symbol
        self.api_key = api_key
        self.start_date = start_date or (date.today() - timedelta(days=365))
        self.end_date = end_date or (date.today() - timedelta(days=1))
        self.sl_fixed = sl_fixed
        self.tp_fixed = tp_fixed
        self.initial_capital = initial_capital
        self.quantity = quantity
        self.data = None
        self.trades = []
        self.active_trade = None
        self.equity_curve = []
        self.equity_dates = []
        self.drawdown_curve = []
        self.drawdown_pct_curve = []
        self.daily_returns = []
        
        # Create results directory
        if base_results_dir:
            self.results_dir = f"{base_results_dir}/{symbol}"
        else:
            self.results_dir = f"backtest_results/{symbol}_{self.start_date}_{self.end_date}"
        
        os.makedirs(self.results_dir, exist_ok=True)
        os.makedirs(f"{self.results_dir}/charts", exist_ok=True)
        
    def download_data_databento(self):
        """Download 5-minute data using Databento"""
        print(f"Downloading {self.symbol} data from Databento...")
        
        try:
            client = Historical(self.api_key)
            
            print(f"Fetching 1-minute data for {self.symbol}...")
            store = client.timeseries.get_range(
                dataset="XNAS.ITCH",
                symbols=self.symbol,
                schema="ohlcv-1m",
                start=self.start_date.isoformat(),
                end=self.end_date.isoformat(),
            )
            
            df = store.to_df()
            
            if df.empty:
                print(f"No data received for {self.symbol}")
                return False
            
            print(f"Downloaded {len(df)} 1-minute candles for {self.symbol}")
            
            df.index = pd.to_datetime(df.index, utc=True)
            df = df.tz_convert("America/New_York")
            df = df.between_time("09:30", "16:00")
            df.index = df.index.tz_localize(None)
            
            self.data = df.resample("5T").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }).dropna()
            
            self.data.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            
            print(f"Final dataset for {self.symbol}: {len(self.data)} 5-minute candles")
            
            if len(self.data) < 50:
                print(f"Warning: Limited data for {self.symbol}")
                return False
            
            raw_data_file = f"{self.results_dir}/{self.symbol}_5min_data.csv"
            self.data.to_csv(raw_data_file)
            
            return True
            
        except Exception as e:
            print(f"Error downloading data for {self.symbol}: {e}")
            return False
    
    def convert_to_candle_objects(self, end_idx):
        """Convert DataFrame rows to CandleData objects for the last 7 candles"""
        start_idx = max(0, end_idx - 6)
        candles = []
        
        for i in range(start_idx, end_idx + 1):
            row = self.data.iloc[i]
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
        """Calculate stop loss and take profit levels using fixed dollar amounts"""
        if signal_type == 'BUY':
            stop_loss = entry_price - self.sl_fixed
            take_profit = entry_price + self.tp_fixed
        else:
            stop_loss = entry_price + self.sl_fixed
            take_profit = entry_price - self.tp_fixed
        
        return stop_loss, take_profit
    
    def check_exit_conditions(self, current_idx):
        """Check if active trade should be closed"""
        if not self.active_trade:
            return False
            
        current_row = self.data.iloc[current_idx]
        current_time = self.data.index[current_idx]
        high = current_row['High']
        low = current_row['Low']
        close = current_row['Close']
        
        if self.active_trade.signal_type == 'BUY':
            if low <= self.active_trade.stop_loss:
                self.active_trade.exit_price = self.active_trade.stop_loss
                self.active_trade.exit_time = current_time
                self.active_trade.exit_reason = 'Stop Loss'
                return True
            elif high >= self.active_trade.take_profit:
                self.active_trade.exit_price = self.active_trade.take_profit
                self.active_trade.exit_time = current_time
                self.active_trade.exit_reason = 'Take Profit'
                return True
        else:
            if high >= self.active_trade.stop_loss:
                self.active_trade.exit_price = self.active_trade.stop_loss
                self.active_trade.exit_time = current_time
                self.active_trade.exit_reason = 'Stop Loss'
                return True
            elif low <= self.active_trade.take_profit:
                self.active_trade.exit_price = self.active_trade.take_profit
                self.active_trade.exit_time = current_time
                self.active_trade.exit_reason = 'Take Profit'
                return True
        
        return False
    
    def calculate_trade_pnl(self, trade):
        """Calculate trade P&L including quantity"""
        if trade.signal_type == 'BUY':
            trade.pnl = trade.exit_price - trade.entry_price
            trade.pnl_pct = (trade.exit_price / trade.entry_price - 1) * 100
            trade.gross_pnl = trade.pnl * trade.quantity
        else:
            trade.pnl = trade.entry_price - trade.exit_price
            trade.pnl_pct = (trade.entry_price / trade.exit_price - 1) * 100
            trade.gross_pnl = trade.pnl * trade.quantity
    
    def update_equity_curve(self, current_time, trade_pnl=0):
        """Update equity curve with current portfolio value"""
        if not self.equity_curve:
            self.equity_curve.append(self.initial_capital)
            self.equity_dates.append(current_time)
        
        current_equity = self.equity_curve[-1] + trade_pnl
        self.equity_curve.append(current_equity)
        self.equity_dates.append(current_time)
    
    def calculate_drawdown_curve(self):
        """Calculate detailed drawdown metrics"""
        if not self.equity_curve:
            return
        
        self.drawdown_curve = []
        self.drawdown_pct_curve = []
        peak = self.initial_capital
        
        for equity_value in self.equity_curve:
            if equity_value > peak:
                peak = equity_value
            
            drawdown = peak - equity_value
            drawdown_pct = (drawdown / peak) * 100 if peak > 0 else 0
            
            self.drawdown_curve.append(drawdown)
            self.drawdown_pct_curve.append(drawdown_pct)
    
    def calculate_portfolio_metrics(self):
        """Calculate comprehensive portfolio performance metrics"""
        if not self.trades:
            return {}
        
        total_trades = len(self.trades)
        winning_trades = [t for t in self.trades if t.gross_pnl > 0]
        losing_trades = [t for t in self.trades if t.gross_pnl < 0]
        
        win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
        
        total_pnl = sum(t.gross_pnl for t in self.trades)
        total_return_pct = (total_pnl / self.initial_capital) * 100
        
        avg_win = np.mean([t.gross_pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t.gross_pnl for t in losing_trades]) if losing_trades else 0
        
        profit_factor = abs(sum(t.gross_pnl for t in winning_trades) / sum(t.gross_pnl for t in losing_trades)) if losing_trades else float('inf')
        
        returns = [t.pnl_pct/100 for t in self.trades]
        avg_return = np.mean(returns) if returns else 0
        std_return = np.std(returns, ddof=1) if len(returns) > 1 else 0
        
        sharpe_ratio = (avg_return / std_return) * np.sqrt(252 * 78) if std_return > 0 else 0
        
        self.calculate_drawdown_curve()
        
        max_drawdown = max(self.drawdown_curve) if self.drawdown_curve else 0
        max_drawdown_pct = max(self.drawdown_pct_curve) if self.drawdown_pct_curve else 0
        
        largest_win = max([t.gross_pnl for t in self.trades]) if self.trades else 0
        largest_loss = min([t.gross_pnl for t in self.trades]) if self.trades else 0
        
        expectancy = (win_rate/100 * avg_win) + ((100-win_rate)/100 * avg_loss)
        
        if len(self.trades) >= 2:
            total_days = (self.trades[-1].exit_time - self.trades[0].entry_time).days
            trades_per_day = total_trades / max(total_days, 1)
        else:
            trades_per_day = 0
        
        recovery_factor = total_return_pct / max_drawdown_pct if max_drawdown_pct > 0 else float('inf')
        calmar_ratio = total_return_pct / max_drawdown_pct if max_drawdown_pct > 0 else float('inf')
        risk_reward_ratio = self.tp_fixed / self.sl_fixed
        
        return {
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'total_return_pct': total_return_pct,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown_pct,
            'largest_win': largest_win,
            'largest_loss': largest_loss,
            'expectancy': expectancy,
            'trades_per_day': trades_per_day,
            'recovery_factor': recovery_factor,
            'calmar_ratio': calmar_ratio,
            'final_equity': self.equity_curve[-1] if self.equity_curve else self.initial_capital,
            'quantity': self.quantity,
            'risk_reward_ratio': risk_reward_ratio
        }
    
    def run_backtest(self):
        """Run the complete backtest"""
        if not self.download_data_databento():
            return False
        
        self.equity_curve = [self.initial_capital]
        self.equity_dates = [self.data.index[0]]
        
        for i in range(7, len(self.data)):
            current_time = self.data.index[i]
            current_row = self.data.iloc[i]
            
            if self.active_trade:
                if self.check_exit_conditions(i):
                    self.calculate_trade_pnl(self.active_trade)
                    self.trades.append(self.active_trade)
                    self.update_equity_curve(current_time, self.active_trade.gross_pnl)
                    self.active_trade = None
            
            if not self.active_trade:
                candles = self.convert_to_candle_objects(i)
                signal = detect_engulfing_signal(candles)
                
                if signal:
                    entry_price = current_row['Close']
                    stop_loss, take_profit = self.calculate_sl_tp(entry_price, signal)
                    
                    self.active_trade = Trade(
                        entry_time=current_time,
                        entry_price=entry_price,
                        signal_type=signal,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        quantity=self.quantity
                    )
        
        if self.active_trade:
            last_row = self.data.iloc[-1]
            self.active_trade.exit_price = last_row['Close']
            self.active_trade.exit_time = self.data.index[-1]
            self.active_trade.exit_reason = 'Market Close'
            self.calculate_trade_pnl(self.active_trade)
            self.trades.append(self.active_trade)
            self.update_equity_curve(self.data.index[-1], self.active_trade.gross_pnl)
            self.active_trade = None
        
        return True

    # ... (keeping all the existing export and plotting methods unchanged)
    def export_trade_logs_csv(self):
        """Export detailed trade logs to CSV"""
        if not self.trades:
            return
        
        trade_data = []
        for i, trade in enumerate(self.trades, 1):
            trade_data.append({
                'Trade_Number': i,
                'Entry_Time': trade.entry_time,
                'Exit_Time': trade.exit_time,
                'Signal_Type': trade.signal_type,
                'Entry_Price': trade.entry_price,
                'Exit_Price': trade.exit_price,
                'Stop_Loss': trade.stop_loss,
                'Take_Profit': trade.take_profit,
                'Quantity': trade.quantity,
                'PnL_Per_Share': trade.pnl,
                'PnL_Percentage': trade.pnl_pct,
                'Gross_PnL': trade.gross_pnl,
                'Exit_Reason': trade.exit_reason,
                'Trade_Duration_Minutes': (trade.exit_time - trade.entry_time).total_seconds() / 60
            })
        
        df_trades = pd.DataFrame(trade_data)
        df_trades['Cumulative_PnL'] = df_trades['Gross_PnL'].cumsum()
        df_trades['Running_Win_Rate'] = (df_trades['Gross_PnL'] > 0).cumsum() / df_trades['Trade_Number'] * 100
        
        trade_logs_file = f"{self.results_dir}/trade_logs_detailed.csv"
        df_trades.to_csv(trade_logs_file, index=False)
        
        return df_trades

    def export_performance_metrics_csv(self):
        """Export performance metrics to CSV"""
        metrics = self.calculate_portfolio_metrics()
        if not metrics:
            return
        
        performance_data = []
        for key, value in metrics.items():
            performance_data.append({
                'Metric': key.replace('_', ' ').title(),
                'Value': value
            })
        
        df_performance = pd.DataFrame(performance_data)
        
        strategy_params = [
            {'Metric': 'Symbol', 'Value': self.symbol},
            {'Metric': 'Start Date', 'Value': self.start_date},
            {'Metric': 'End Date', 'Value': self.end_date},
            {'Metric': 'Initial Capital', 'Value': self.initial_capital},
            {'Metric': 'Fixed Stop Loss', 'Value': self.sl_fixed},
            {'Metric': 'Fixed Take Profit', 'Value': self.tp_fixed}
        ]
        
        df_params = pd.DataFrame(strategy_params)
        df_combined = pd.concat([df_params, df_performance], ignore_index=True)
        
        performance_file = f"{self.results_dir}/performance_metrics.csv"
        df_combined.to_csv(performance_file, index=False)
        
        return df_combined

    def export_equity_curve_csv(self):
        """Export equity curve data to CSV"""
        if not self.equity_curve or not self.equity_dates:
            return
        
        self.calculate_drawdown_curve()
        
        equity_data = []
        for i in range(len(self.equity_curve)):
            equity_data.append({
                'Timestamp': self.equity_dates[i],
                'Portfolio_Value': self.equity_curve[i],
                'Absolute_Drawdown': self.drawdown_curve[i] if i < len(self.drawdown_curve) else 0,
                'Percentage_Drawdown': self.drawdown_pct_curve[i] if i < len(self.drawdown_pct_curve) else 0,
                'Total_Return_Pct': ((self.equity_curve[i] / self.initial_capital) - 1) * 100
            })
        
        df_equity = pd.DataFrame(equity_data)
        
        equity_file = f"{self.results_dir}/equity_curve.csv"
        df_equity.to_csv(equity_file, index=False)
        
        return df_equity

    def export_all_csvs(self):
        """Export all CSV files"""
        self.export_trade_logs_csv()
        self.export_performance_metrics_csv()
        self.export_equity_curve_csv()

    def plot_and_save_charts(self):
        """Generate and save all charts (simplified version for multi-ticker)"""
        if not self.trades:
            return
        
        # Only create essential charts for individual tickers to save time
        plt.figure(figsize=(15, 8))
        plt.plot(self.data.index, self.data['Close'], alpha=0.7, linewidth=0.8, color='black')
        
        for trade in self.trades:
            color = 'green' if trade.signal_type == 'BUY' else 'red'
            marker = '^' if trade.signal_type == 'BUY' else 'v'
            plt.scatter(trade.entry_time, trade.entry_price, color=color, marker=marker, s=50, alpha=0.8)
        
        plt.title(f'{self.symbol} - Price Chart with Trade Signals')
        plt.ylabel('Price ($)')
        plt.xlabel('Date')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(f"{self.results_dir}/charts/price_chart_with_signals.png", dpi=150, bbox_inches='tight')
        plt.close()
        
        # Equity curve
        plt.figure(figsize=(12, 6))
        plt.plot(self.equity_dates, self.equity_curve, color='blue', linewidth=2)
        plt.axhline(y=self.initial_capital, color='black', linestyle='--', alpha=0.5)
        plt.title(f'{self.symbol} - Equity Curve')
        plt.ylabel('Portfolio Value ($)')
        plt.xlabel('Date')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(f"{self.results_dir}/charts/equity_curve.png", dpi=150, bbox_inches='tight')
        plt.close()


class MultiTickerBacktester:
    """Enhanced multi-ticker backtester with consolidated reporting"""
    
    def __init__(self, symbols, api_key, start_date=None, end_date=None, sl_fixed=1.0, tp_fixed=2.0, 
                 initial_capital=10000, quantity=100, max_workers=5):
        self.symbols = symbols
        self.api_key = api_key
        self.start_date = start_date or (date.today() - timedelta(days=365))
        self.end_date = end_date or (date.today() - timedelta(days=1))
        self.sl_fixed = sl_fixed
        self.tp_fixed = tp_fixed
        self.initial_capital = initial_capital
        self.quantity = quantity
        self.max_workers = max_workers
        
        # Results storage
        self.backtesters = {}
        self.successful_symbols = []
        self.failed_symbols = []
        
        # Create master results directory
        self.master_results_dir = f"multi_ticker_results_{self.start_date}_{self.end_date}"
        os.makedirs(self.master_results_dir, exist_ok=True)
        os.makedirs(f"{self.master_results_dir}/combined_analysis", exist_ok=True)
        
    def run_single_ticker(self, symbol):
        """Run backtest for a single ticker"""
        try:
            print(f"Starting backtest for {symbol}...")
            backtester = EngulfingBacktester(
                symbol=symbol,
                api_key=self.api_key,
                start_date=self.start_date,
                end_date=self.end_date,
                sl_fixed=self.sl_fixed,
                tp_fixed=self.tp_fixed,
                initial_capital=self.initial_capital,
                quantity=self.quantity,
                base_results_dir=self.master_results_dir
            )
            
            success = backtester.run_backtest()
            if success:
                backtester.export_all_csvs()
                backtester.plot_and_save_charts()
                print(f"✓ Completed backtest for {symbol}")
                return symbol, backtester
            else:
                print(f"✗ Failed backtest for {symbol}")
                return symbol, None
                
        except Exception as e:
            print(f"✗ Error with {symbol}: {e}")
            return symbol, None
    
    def run_all_backtests(self):
        """Run backtests for all symbols with progress tracking"""
        print(f"Starting multi-ticker backtest for {len(self.symbols)} symbols...")
        print(f"Date range: {self.start_date} to {self.end_date}")
        print(f"Strategy: Fixed SL=${self.sl_fixed}, TP=${self.tp_fixed}")
        print("="*80)
        
        # Sequential processing to avoid API rate limits
        for i, symbol in enumerate(self.symbols, 1):
            print(f"\n[{i}/{len(self.symbols)}] Processing {symbol}...")
            
            symbol_result, backtester = self.run_single_ticker(symbol)
            
            if backtester:
                self.backtesters[symbol_result] = backtester
                self.successful_symbols.append(symbol_result)
            else:
                self.failed_symbols.append(symbol_result)
            
            # Add small delay to be respectful to API
            time.sleep(1)
        
        print(f"\n{'='*80}")
        print(f"Multi-ticker backtest completed!")
        print(f"Successful: {len(self.successful_symbols)} symbols")
        print(f"Failed: {len(self.failed_symbols)} symbols")
        
        if self.failed_symbols:
            print(f"Failed symbols: {', '.join(self.failed_symbols)}")
    
    def create_combined_performance_report(self):
        """Create comprehensive combined performance analysis"""
        if not self.successful_symbols:
            print("No successful backtests to analyze.")
            return
        
        print("\nGenerating combined performance report...")
        
        # Collect all performance metrics
        combined_metrics = []
        
        for symbol in self.successful_symbols:
            backtester = self.backtesters[symbol]
            metrics = backtester.calculate_portfolio_metrics()
            
            if metrics:
                metrics['Symbol'] = symbol
                combined_metrics.append(metrics)
        
        if not combined_metrics:
            print("No performance metrics available.")
            return
        
        # Create DataFrame
        df_combined = pd.DataFrame(combined_metrics)
        
        # Reorder columns
        cols = ['Symbol'] + [col for col in df_combined.columns if col != 'Symbol']
        df_combined = df_combined[cols]
        
        # Sort by total return
        df_combined = df_combined.sort_values('total_return_pct', ascending=False)
        
        # Export combined metrics
        combined_file = f"{self.master_results_dir}/combined_analysis/combined_performance_metrics.csv"
        df_combined.to_csv(combined_file, index=False)
        
        # Create summary statistics
        summary_stats = {
            'Metric': [
                'Average Total Return (%)',
                'Best Performer (Return %)',
                'Worst Performer (Return %)',
                'Average Win Rate (%)',
                'Best Win Rate (%)',
                'Worst Win Rate (%)',
                'Average Sharpe Ratio',
                'Best Sharpe Ratio',
                'Worst Sharpe Ratio',
                'Average Max Drawdown (%)',
                'Best Max Drawdown (%)',
                'Worst Max Drawdown (%)',
                'Total Combined Trades',
                'Average Trades per Symbol',
                'Symbols with Positive Returns',
                'Symbols with Negative Returns',
                'Average Profit Factor',
                'Success Rate (% Profitable Symbols)'
            ],
            'Value': [
                df_combined['total_return_pct'].mean(),
                df_combined['total_return_pct'].max(),
                df_combined['total_return_pct'].min(),
                df_combined['win_rate'].mean(),
                df_combined['win_rate'].max(),
                df_combined['win_rate'].min(),
                df_combined['sharpe_ratio'].mean(),
                df_combined['sharpe_ratio'].max(),
                df_combined['sharpe_ratio'].min(),
                df_combined['max_drawdown_pct'].mean(),
                df_combined['max_drawdown_pct'].min(),
                df_combined['max_drawdown_pct'].max(),
                df_combined['total_trades'].sum(),
                df_combined['total_trades'].mean(),
                len(df_combined[df_combined['total_return_pct'] > 0]),
                len(df_combined[df_combined['total_return_pct'] < 0]),
                df_combined['profit_factor'].mean(),
                (len(df_combined[df_combined['total_return_pct'] > 0]) / len(df_combined)) * 100
            ]
        }
        
        df_summary = pd.DataFrame(summary_stats)
        
        # Export summary
        summary_file = f"{self.master_results_dir}/combined_analysis/portfolio_summary_statistics.csv"
        df_summary.to_csv(summary_file, index=False)
        
        # Create ranking tables
        rankings = {
            'Top_Performers_Return': df_combined[['Symbol', 'total_return_pct', 'win_rate', 'total_trades']].head(10),
            'Top_Performers_Sharpe': df_combined[['Symbol', 'sharpe_ratio', 'total_return_pct', 'max_drawdown_pct']].head(10),
            'Top_Win_Rates': df_combined[['Symbol', 'win_rate', 'total_return_pct', 'total_trades']].head(10),
            'Lowest_Drawdowns': df_combined[['Symbol', 'max_drawdown_pct', 'total_return_pct', 'win_rate']].head(10)
        }
        
        # Export rankings
        for ranking_name, ranking_df in rankings.items():
            ranking_file = f"{self.master_results_dir}/combined_analysis/{ranking_name.lower()}.csv"
            ranking_df.to_csv(ranking_file, index=False)
        
        print(f"Combined performance report saved to: {self.master_results_dir}/combined_analysis/")
        return df_combined, df_summary
    
    def create_combined_visualizations(self):
        """Create comprehensive visualizations for multi-ticker analysis"""
        if not self.successful_symbols:
            return
        
        print("Generating combined visualizations...")
        
        # Collect performance data
        performance_data = []
        for symbol in self.successful_symbols:
            metrics = self.backtesters[symbol].calculate_portfolio_metrics()
            if metrics:
                metrics['Symbol'] = symbol
                performance_data.append(metrics)
        
        df_perf = pd.DataFrame(performance_data)
        
        # Set up the plotting style
        plt.style.use('default')
        sns.set_palette("husl")
        
        # Chart 1: Performance Overview Dashboard
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(20, 16))
        
        # Total Return vs Max Drawdown
        scatter = ax1.scatter(df_perf['max_drawdown_pct'], df_perf['total_return_pct'], 
                             s=df_perf['total_trades']*5, alpha=0.6, c=df_perf['win_rate'], 
                             cmap='RdYlGn', vmin=0, vmax=100)
        ax1.axhline(y=0, color='black', linestyle='--', alpha=0.5)
        ax1.axvline(x=0, color='black', linestyle='--', alpha=0.5)
        ax1.set_xlabel('Max Drawdown (%)')
        ax1.set_ylabel('Total Return (%)')
        ax1.set_title('Risk vs Return Analysis\n(Size=Total Trades, Color=Win Rate)')
        ax1.grid(True, alpha=0.3)
        
        # Add symbol labels for extreme points
        for i, row in df_perf.iterrows():
            if row['total_return_pct'] > df_perf['total_return_pct'].quantile(0.8) or \
               row['total_return_pct'] < df_perf['total_return_pct'].quantile(0.2):
                ax1.annotate(row['Symbol'], (row['max_drawdown_pct'], row['total_return_pct']), 
                           xytext=(5, 5), textcoords='offset points', fontsize=8)
        
        plt.colorbar(scatter, ax=ax1, label='Win Rate (%)')
        
        # Return Distribution
        ax2.hist(df_perf['total_return_pct'], bins=15, alpha=0.7, edgecolor='black')
        ax2.axvline(x=0, color='red', linestyle='--', alpha=0.7, label='Breakeven')
        ax2.axvline(x=df_perf['total_return_pct'].mean(), color='green', linestyle='--', 
                   alpha=0.7, label=f'Mean ({df_perf["total_return_pct"].mean():.1f}%)')
        ax2.set_xlabel('Total Return (%)')
        ax2.set_ylabel('Number of Symbols')
        ax2.set_title('Return Distribution Across All Symbols')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # Win Rate vs Profit Factor
        ax3.scatter(df_perf['win_rate'], df_perf['profit_factor'], alpha=0.7, s=100)
        ax3.set_xlabel('Win Rate (%)')
        ax3.set_ylabel('Profit Factor')
        ax3.set_title('Win Rate vs Profit Factor')
        ax3.grid(True, alpha=0.3)
        
        # Add breakeven win rate line
        breakeven_wr = self.sl_fixed / (self.sl_fixed + self.tp_fixed) * 100
        ax3.axvline(x=breakeven_wr, color='red', linestyle='--', alpha=0.7, 
                   label=f'Breakeven WR ({breakeven_wr:.1f}%)')
        ax3.legend()
        
        # Top and Bottom Performers
        top_5 = df_perf.nlargest(5, 'total_return_pct')
        bottom_5 = df_perf.nsmallest(5, 'total_return_pct')
        combined_extreme = pd.concat([top_5, bottom_5])
        
        colors = ['green' if x > 0 else 'red' for x in combined_extreme['total_return_pct']]
        ax4.barh(range(len(combined_extreme)), combined_extreme['total_return_pct'], color=colors, alpha=0.7)
        ax4.set_yticks(range(len(combined_extreme)))
        ax4.set_yticklabels(combined_extreme['Symbol'])
        ax4.set_xlabel('Total Return (%)')
        ax4.set_title('Top 5 Best & Worst Performers')
        ax4.axvline(x=0, color='black', linestyle='-', alpha=0.5)
        ax4.grid(True, alpha=0.3, axis='x')
        
        plt.tight_layout()
        plt.savefig(f"{self.master_results_dir}/combined_analysis/performance_overview_dashboard.png", 
                   dpi=300, bbox_inches='tight')
        plt.close()
        
        # Chart 2: Combined Equity Curves
        plt.figure(figsize=(16, 10))
        
        # Calculate equal-weighted portfolio
        combined_equity = None
        combined_dates = None
        
        for i, symbol in enumerate(self.successful_symbols[:10]):  # Limit to first 10 for readability
            backtester = self.backtesters[symbol]
            if backtester.equity_curve and backtester.equity_dates:
                # Individual equity curves (light lines)
                returns = [(eq / self.initial_capital - 1) * 100 for eq in backtester.equity_curve]
                plt.plot(backtester.equity_dates, returns, alpha=0.3, linewidth=1, label=symbol if i < 5 else "")
                
                # Combine for portfolio calculation
                if combined_equity is None:
                    combined_equity = np.array(returns)
                    combined_dates = backtester.equity_dates
                else:
                    # Align dates and average returns (simplified approach)
                    if len(returns) == len(combined_equity):
                        combined_equity += np.array(returns)
        
        # Plot combined portfolio (equal weight)
        if combined_equity is not None:
            combined_equity = combined_equity / len(self.successful_symbols)
            plt.plot(combined_dates, combined_equity, color='black', linewidth=3, 
                    label=f'Equal-Weighted Portfolio (n={len(self.successful_symbols)})', alpha=0.8)
        
        plt.axhline(y=0, color='red', linestyle='--', alpha=0.5, label='Breakeven')
        plt.title('Individual Symbol Returns vs Combined Portfolio', fontsize=16)
        plt.ylabel('Return (%)', fontsize=12)
        plt.xlabel('Date', fontsize=12)
        plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(f"{self.master_results_dir}/combined_analysis/combined_equity_curves.png", 
                   dpi=300, bbox_inches='tight')
        plt.close()
        
        # Chart 3: Strategy Effectiveness Analysis
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(18, 14))
        
        # Win Rate Distribution
        ax1.hist(df_perf['win_rate'], bins=12, alpha=0.7, edgecolor='black', color='skyblue')
        ax1.axvline(x=breakeven_wr, color='red', linestyle='--', alpha=0.7, 
                   label=f'Breakeven ({breakeven_wr:.1f}%)')
        ax1.axvline(x=df_perf['win_rate'].mean(), color='green', linestyle='--', alpha=0.7,
                   label=f'Average ({df_perf["win_rate"].mean():.1f}%)')
        ax1.set_xlabel('Win Rate (%)')
        ax1.set_ylabel('Number of Symbols')
        ax1.set_title('Win Rate Distribution')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Trade Frequency Analysis
        ax2.scatter(df_perf['total_trades'], df_perf['total_return_pct'], alpha=0.7, s=100)
        ax2.set_xlabel('Total Trades')
        ax2.set_ylabel('Total Return (%)')
        ax2.set_title('Trade Frequency vs Performance')
        ax2.grid(True, alpha=0.3)
        
        # Sharpe Ratio Distribution
        valid_sharpe = df_perf[df_perf['sharpe_ratio'] != np.inf]['sharpe_ratio']
        ax3.hist(valid_sharpe, bins=12, alpha=0.7, edgecolor='black', color='lightcoral')
        ax3.axvline(x=0, color='black', linestyle='--', alpha=0.5)
        ax3.axvline(x=valid_sharpe.mean(), color='green', linestyle='--', alpha=0.7,
                   label=f'Average ({valid_sharpe.mean():.2f})')
        ax3.set_xlabel('Sharpe Ratio')
        ax3.set_ylabel('Number of Symbols')
        ax3.set_title('Risk-Adjusted Return Distribution')
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        
        # Signal Type Effectiveness (if available)
        buy_performance = []
        sell_performance = []
        
        for symbol in self.successful_symbols:
            backtester = self.backtesters[symbol]
            buy_trades = [t for t in backtester.trades if t.signal_type == 'BUY']
            sell_trades = [t for t in backtester.trades if t.signal_type == 'SELL']
            
            if buy_trades:
                buy_pnl = sum(t.gross_pnl for t in buy_trades)
                buy_performance.append(buy_pnl)
            if sell_trades:
                sell_pnl = sum(t.gross_pnl for t in sell_trades)
                sell_performance.append(sell_pnl)
        
        signal_data = {
            'BUY Signals': buy_performance,
            'SELL Signals': sell_performance
        }
        
        box_data = [buy_performance, sell_performance]
        ax4.boxplot(box_data, labels=['BUY', 'SELL'])
        ax4.axhline(y=0, color='red', linestyle='--', alpha=0.5)
        ax4.set_ylabel('Total P&L per Symbol ($)')
        ax4.set_title('Signal Type Performance Comparison')
        ax4.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(f"{self.master_results_dir}/combined_analysis/strategy_effectiveness_analysis.png", 
                   dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"Combined visualizations saved to: {self.master_results_dir}/combined_analysis/")
    
    def generate_master_report(self):
        """Generate comprehensive master report"""
        if not self.successful_symbols:
            print("No successful backtests to report on.")
            return
        
        print("\n" + "="*100)
        print("MULTI-TICKER ENGULFING STRATEGY BACKTEST RESULTS")
        print("="*100)
        
        # Basic Information
        print(f"Analysis Period: {self.start_date} to {self.end_date}")
        print(f"Strategy: Engulfing Pattern with Fixed SL/TP")
        print(f"Fixed Stop Loss: ${self.sl_fixed}")
        print(f"Fixed Take Profit: ${self.tp_fixed}")
        print(f"Risk/Reward Ratio: 1:{self.tp_fixed/self.sl_fixed:.1f}")
        print(f"Position Size: {self.quantity} shares per trade")
        print(f"Initial Capital per Symbol: ${self.initial_capital:,}")
        
        print(f"\nExecution Summary:")
        print(f"Total Symbols Attempted: {len(self.symbols)}")
        print(f"Successful Backtests: {len(self.successful_symbols)}")
        print(f"Failed Backtests: {len(self.failed_symbols)}")
        print(f"Success Rate: {(len(self.successful_symbols)/len(self.symbols)*100):.1f}%")
        
        if self.failed_symbols:
            print(f"Failed Symbols: {', '.join(self.failed_symbols)}")
        
        # Collect performance metrics
        all_metrics = []
        for symbol in self.successful_symbols:
            metrics = self.backtesters[symbol].calculate_portfolio_metrics()
            if metrics:
                metrics['Symbol'] = symbol
                all_metrics.append(metrics)
        
        if not all_metrics:
            print("\nNo performance metrics available for analysis.")
            return
        
        df_all = pd.DataFrame(all_metrics)
        
        # Portfolio-Level Statistics
        print(f"\n{'PORTFOLIO-LEVEL PERFORMANCE':<50}")
        print("-" * 80)
        
        total_capital_deployed = len(self.successful_symbols) * self.initial_capital
        total_final_value = df_all['final_equity'].sum()
        portfolio_return = ((total_final_value / total_capital_deployed) - 1) * 100
        
        print(f"Total Capital Deployed: ${total_capital_deployed:,.2f}")
        print(f"Total Final Portfolio Value: ${total_final_value:,.2f}")
        print(f"Overall Portfolio Return: {portfolio_return:+.2f}%")
        
        positive_symbols = len(df_all[df_all['total_return_pct'] > 0])
        negative_symbols = len(df_all[df_all['total_return_pct'] < 0])
        
        print(f"Profitable Symbols: {positive_symbols}/{len(self.successful_symbols)} ({positive_symbols/len(self.successful_symbols)*100:.1f}%)")
        print(f"Unprofitable Symbols: {negative_symbols}/{len(self.successful_symbols)} ({negative_symbols/len(self.successful_symbols)*100:.1f}%)")
        
        # Average Performance Metrics
        print(f"\n{'AVERAGE PERFORMANCE METRICS':<50}")
        print("-" * 80)
        print(f"Average Return per Symbol: {df_all['total_return_pct'].mean():.2f}%")
        print(f"Median Return per Symbol: {df_all['total_return_pct'].median():.2f}%")
        print(f"Standard Deviation of Returns: {df_all['total_return_pct'].std():.2f}%")
        print(f"Average Win Rate: {df_all['win_rate'].mean():.1f}%")
        print(f"Average Sharpe Ratio: {df_all[df_all['sharpe_ratio'] != np.inf]['sharpe_ratio'].mean():.2f}")
        print(f"Average Max Drawdown: {df_all['max_drawdown_pct'].mean():.2f}%")
        print(f"Average Profit Factor: {df_all['profit_factor'].mean():.2f}")
        
        # Trading Activity Summary
        print(f"\n{'TRADING ACTIVITY SUMMARY':<50}")
        print("-" * 80)
        total_trades = df_all['total_trades'].sum()
        avg_trades_per_symbol = df_all['total_trades'].mean()
        
        print(f"Total Trades Across All Symbols: {total_trades}")
        print(f"Average Trades per Symbol: {avg_trades_per_symbol:.1f}")
        print(f"Most Active Symbol: {df_all.loc[df_all['total_trades'].idxmax(), 'Symbol']} ({df_all['total_trades'].max()} trades)")
        print(f"Least Active Symbol: {df_all.loc[df_all['total_trades'].idxmin(), 'Symbol']} ({df_all['total_trades'].min()} trades)")
        
        # Top Performers
        print(f"\n{'TOP PERFORMERS':<50}")
        print("-" * 80)
        
        top_5_return = df_all.nlargest(5, 'total_return_pct')
        print("By Total Return:")
        for idx, row in top_5_return.iterrows():
            print(f"  {row['Symbol']}: {row['total_return_pct']:+.2f}% (WR: {row['win_rate']:.1f}%, Trades: {row['total_trades']})")
        
        top_5_sharpe = df_all[df_all['sharpe_ratio'] != np.inf].nlargest(5, 'sharpe_ratio')
        print("\nBy Sharpe Ratio:")
        for idx, row in top_5_sharpe.iterrows():
            print(f"  {row['Symbol']}: {row['sharpe_ratio']:.2f} (Return: {row['total_return_pct']:+.2f}%, DD: {row['max_drawdown_pct']:.1f}%)")
        
        # Worst Performers
        print(f"\n{'WORST PERFORMERS':<50}")
        print("-" * 80)
        
        bottom_5_return = df_all.nsmallest(5, 'total_return_pct')
        print("By Total Return:")
        for idx, row in bottom_5_return.iterrows():
            print(f"  {row['Symbol']}: {row['total_return_pct']:+.2f}% (WR: {row['win_rate']:.1f}%, DD: {row['max_drawdown_pct']:.1f}%)")
        
        # Strategy Effectiveness Analysis
        print(f"\n{'STRATEGY EFFECTIVENESS ANALYSIS':<50}")
        print("-" * 80)
        
        breakeven_wr = self.sl_fixed / (self.sl_fixed + self.tp_fixed) * 100
        above_breakeven = len(df_all[df_all['win_rate'] > breakeven_wr])
        
        print(f"Required Breakeven Win Rate: {breakeven_wr:.1f}%")
        print(f"Symbols Above Breakeven WR: {above_breakeven}/{len(self.successful_symbols)} ({above_breakeven/len(self.successful_symbols)*100:.1f}%)")
        print(f"Average Win Rate vs Breakeven: {df_all['win_rate'].mean() - breakeven_wr:+.1f} percentage points")
        
        # Risk Analysis
        print(f"\n{'RISK ANALYSIS':<50}")
        print("-" * 80)
        print(f"Average Maximum Drawdown: {df_all['max_drawdown_pct'].mean():.2f}%")
        print(f"Worst Maximum Drawdown: {df_all['max_drawdown_pct'].max():.2f}% ({df_all.loc[df_all['max_drawdown_pct'].idxmax(), 'Symbol']})")
        print(f"Best Maximum Drawdown: {df_all['max_drawdown_pct'].min():.2f}% ({df_all.loc[df_all['max_drawdown_pct'].idxmin(), 'Symbol']})")
        
        high_risk_symbols = len(df_all[df_all['max_drawdown_pct'] > 20])
        print(f"High Risk Symbols (>20% DD): {high_risk_symbols}/{len(self.successful_symbols)}")
        
        print(f"\n{'EXPORT SUMMARY':<50}")
        print("-" * 80)
        print(f"Individual Results: {self.master_results_dir}/[SYMBOL]/")
        print(f"Combined Analysis: {self.master_results_dir}/combined_analysis/")
        print(f"Performance Metrics: combined_performance_metrics.csv")
        print(f"Summary Statistics: portfolio_summary_statistics.csv")
        print(f"Visual Analysis: *.png files")
        
        print("\n" + "="*100)
    
    def run_complete_multi_ticker_analysis(self):
        """Run complete multi-ticker analysis with all reports and visualizations"""
        print("="*100)
        print("ENHANCED MULTI-TICKER ENGULFING STRATEGY BACKTESTER")
        print("Data Source: Databento API")
        print("="*100)
        
        # Run all backtests
        self.run_all_backtests()
        
        if not self.successful_symbols:
            print("No successful backtests completed. Analysis cannot proceed.")
            return False
        
        # Create combined reports
        self.create_combined_performance_report()
        
        # Create visualizations
        try:
            self.create_combined_visualizations()
        except Exception as e:
            print(f"Warning: Error creating visualizations: {e}")
        
        # Generate master report
        self.generate_master_report()
        
        # Create master summary file
        summary_file = f"{self.master_results_dir}/MASTER_SUMMARY.txt"
        with open(summary_file, 'w') as f:
            f.write("MULTI-TICKER ENGULFING STRATEGY BACKTEST\n")
            f.write("="*50 + "\n\n")
            f.write(f"Analysis Date: {date.today()}\n")
            f.write(f"Period: {self.start_date} to {self.end_date}\n")
            f.write(f"Strategy: Fixed SL=${self.sl_fixed}, TP=${self.tp_fixed}\n")
            f.write(f"Symbols Analyzed: {len(self.symbols)}\n")
            f.write(f"Successful: {len(self.successful_symbols)}\n")
            f.write(f"Failed: {len(self.failed_symbols)}\n\n")
            
            if self.successful_symbols:
                df_metrics = pd.DataFrame([self.backtesters[s].calculate_portfolio_metrics() 
                                         for s in self.successful_symbols])
                f.write(f"Portfolio Performance:\n")
                f.write(f"- Average Return: {df_metrics['total_return_pct'].mean():.2f}%\n")
                f.write(f"- Success Rate: {len(df_metrics[df_metrics['total_return_pct'] > 0])/len(df_metrics)*100:.1f}%\n")
                f.write(f"- Best Performer: {df_metrics['total_return_pct'].max():.2f}%\n")
                f.write(f"- Worst Performer: {df_metrics['total_return_pct'].min():.2f}%\n")
        
        print(f"\n🎉 Multi-ticker analysis completed successfully!")
        print(f"📂 Check results in: {self.master_results_dir}/")
        print(f"📊 Individual ticker reports in respective subdirectories")
        print(f"📈 Combined analysis in: combined_analysis/")
        
        return True


# Main execution function
def main_multi_ticker():
    """Main execution function for multi-ticker backtest"""
    
    # Configuration
    API_KEY = "db-V6dFxJLQ3LHWH9HNKGYqk7TDCf7tn"  # Your Databento API key
    
    # List of symbols to analyze (you can modify this list)
    SYMBOLS = [
        'CAT', 'CRM', 'GOOGL', 'AAPL', 'AMD',
        'HD', 'URI', 'AVGO', 'JPM', 'AMZN'
    ]
    
    # Date range (last 6 months for example)
    END_DATE = date.today() - timedelta(days=1)
    START_DATE = END_DATE - timedelta(days=365)  # 12 months
    
    # Strategy parameters
    STOP_LOSS = 1.0      # Fixed $2 stop loss
    TAKE_PROFIT = 2.0    # Fixed $2 take profit
    QUANTITY = 100       # 100 shares per trade
    INITIAL_CAPITAL = 10000  # $10,000 starting capital per symbol
    
    print("Starting Multi-Ticker Engulfing Strategy Backtest")
    print("=" * 80)
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"Total Symbols: {len(SYMBOLS)}")
    print(f"Date Range: {START_DATE} to {END_DATE}")
    print(f"Stop Loss: ${STOP_LOSS}")
    print(f"Take Profit: ${TAKE_PROFIT}")
    print(f"Position Size: {QUANTITY} shares")
    print(f"Initial Capital per Symbol: ${INITIAL_CAPITAL:,}")
    print(f"Total Capital Deployed: ${INITIAL_CAPITAL * len(SYMBOLS):,}")
    
    # Initialize multi-ticker backtester
    multi_backtester = MultiTickerBacktester(
        symbols=SYMBOLS,
        api_key=API_KEY,
        start_date=START_DATE,
        end_date=END_DATE,
        sl_fixed=STOP_LOSS,
        tp_fixed=TAKE_PROFIT,
        initial_capital=INITIAL_CAPITAL,
        quantity=QUANTITY,
        max_workers=3  # Limit concurrent API calls
    )
    
    # Run complete multi-ticker analysis
    success = multi_backtester.run_complete_multi_ticker_analysis()
    
    if success:
        print("\n✅ Multi-ticker backtest completed successfully!")
        print(f"📂 Check results in: {multi_backtester.master_results_dir}/")
        print("\n📋 Available Reports:")
        print("  - Individual ticker results in separate folders")
        print("  - combined_analysis/combined_performance_metrics.csv")
        print("  - combined_analysis/portfolio_summary_statistics.csv")
        print("  - combined_analysis/*.png (visualization charts)")
        print("  - MASTER_SUMMARY.txt")
    else:
        print("\n❌ Multi-ticker backtest failed or no data available.")
        print("Please check your API key and symbol list.")


if __name__ == "__main__":
    main_multi_ticker()