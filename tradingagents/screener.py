import os
import logging
import pickle
import datetime
from typing import Dict, List, Optional, Set
import pandas as pd
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger('tradingagents.screener')

# Configuration Constants
SCREENER_ENABLED = os.getenv('SCREENER_ENABLED', 'true').lower() == 'true'
SCREENER_SCAN_INTERVAL_MIN = int(os.getenv('SCREENER_SCAN_INTERVAL_MIN', '30'))
SCREENER_CRYPTO_INTERVAL_H = int(os.getenv('SCREENER_CRYPTO_INTERVAL_H', '4'))
SCREENER_COOLDOWN_HOURS = float(os.getenv('SCREENER_COOLDOWN_HOURS', '24.0'))
SCREENER_MIN_SCORE_STOCK = float(os.getenv('SCREENER_MIN_SCORE_STOCK', '7.0'))
SCREENER_MIN_SCORE_CRYPTO = float(os.getenv('SCREENER_MIN_SCORE_CRYPTO', '4.0'))
SCREENER_MAX_CANDIDATES = int(os.getenv('SCREENER_MAX_CANDIDATES', '3'))
SCREENER_MIN_VOLUME = float(os.getenv('SCREENER_MIN_VOLUME', '500000'))
SCREENER_MAX_UNIVERSE = int(os.getenv('SCREENER_MAX_UNIVERSE', '0'))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_PROJECT_ROOT, "data", "screener_cache")
_OHLCV_CACHE_FILE = os.path.join(_CACHE_DIR, "ohlcv_cache.pkl")

DEFAULT_US_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "INTC", "NFLX",
    "PYPL", "DIS", "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "BA", "CAT", "DE",
    "XOM", "CVX", "COP", "PFE", "JNJ", "UNH", "LLY", "MRK", "ABBV", "PG", "KO",
    "PEP", "COST", "WMT", "TGT", "HD", "LOW", "NKE", "ORCL", "CRM", "ADBE", "CSCO",
    "AVGO", "QCOM", "TXN", "AMAT", "MU", "PANW", "SNOW", "PLTR", "UBER", "ABNB",
    "SONY", "NTDOY", "ASML", "TSM", "NVO", "AZN", "BABA", "BIDU", "PDD", "JD",
    "SAP", "SNY", "BTI", "RIO", "BHP", "VALE", "BP", "SHEL", "TTE"
]

DEFAULT_CRYPTO_UNIVERSE = [
    "BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "DOGE-USD", "ADA-USD",
    "LINK-USD", "SUI-USD", "NEAR-USD", "XRP-USD", "BNB-USD", "DOT-USD",
    "MATIC-USD", "UNI-USD", "ATOM-USD", "LTC-USD", "PEPE-USD", "SHIB-USD",
    "APT-USD", "INJ-USD"
]

_latest_scan_status = {}

def load_universe() -> Dict[str, str]:
    """
    Loads all tradeable assets dynamically from Alpaca API and filters them.
    Returns dict mapping symbol -> asset_type ('stock' or 'crypto').
    """
    try:
        from tradingagents.dataflows.alpaca_utils import AlpacaUtils
        dynamic = AlpacaUtils.get_tradeable_assets()
        if dynamic and len(dynamic) > 0:
            universe = dict(dynamic)
            
            # Ensure DEFAULT list items are always included if missing
            for ticker in DEFAULT_US_UNIVERSE:
                if ticker not in universe:
                    universe[ticker] = 'stock'
            for ticker in DEFAULT_CRYPTO_UNIVERSE:
                if ticker not in universe:
                    universe[ticker] = 'crypto'
            
            # If SCREENER_MAX_UNIVERSE is configured > 0, cap to that number; otherwise return full market
            if SCREENER_MAX_UNIVERSE > 0 and len(universe) > SCREENER_MAX_UNIVERSE:
                keys = list(universe.keys())[:SCREENER_MAX_UNIVERSE]
                universe = {k: universe[k] for k in keys}
                
            logger.info(f"Loaded full tradeable universe from Alpaca: {len(universe)} tickers.")
            return universe
    except Exception as e:
        logger.debug(f"Failed to load dynamic universe from AlpacaUtils: {e}")
        pass
    
    # Fallback to DEFAULT lists
    logger.info("Using default universe lists.")
    universe = {}
    for ticker in DEFAULT_US_UNIVERSE:
        universe[ticker] = 'stock'
    for ticker in DEFAULT_CRYPTO_UNIVERSE:
        universe[ticker] = 'crypto'
    return universe

def _download_batch(tickers: List[str], period: str, interval: str) -> Dict[str, pd.DataFrame]:
    if not tickers:
        return {}
    import time
    try:
        data = yf.download(tickers, period=period, interval=interval, group_by='ticker', threads=True, progress=False)
        result = {}
        if len(tickers) == 1:
            if not data.empty:
                result[tickers[0]] = data
        else:
            for t in tickers:
                if t in data and not data[t].empty:
                    df = data[t].dropna(how='all')
                    if not df.empty:
                        result[t] = df
        return result
    except Exception as e:
        logger.warning(f"Batch download warning for {len(tickers)} tickers: {e}")
        return {}

def fetch_bulk_ohlcv(tickers: List[str], period: str = '60d', interval: str = '1d') -> Dict[str, pd.DataFrame]:
    """
    Downloads OHLCV data in batches of 50 using yfinance with rate-limit protection.
    Missing tickers: full download with period='60d'
    Cached tickers: incremental 2-day update
    Enforces max 60 trading days per ticker in cache
    """
    import time
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache: Dict[str, pd.DataFrame] = {}
    
    if os.path.exists(_OHLCV_CACHE_FILE):
        try:
            with open(_OHLCV_CACHE_FILE, 'rb') as f:
                cache = pickle.load(f)
        except Exception as e:
            logger.warning(f"Could not load cache: {e}")
            cache = {}
            
    missing_tickers = []
    update_tickers = []
    
    for t in tickers:
        if t not in cache or cache[t].empty or len(cache[t]) < 20:
            missing_tickers.append(t)
        else:
            update_tickers.append(t)
            
    logger.info(f"Fetching OHLCV: {len(missing_tickers)} missing, {len(update_tickers)} to update.")
    
    new_data = {}
    batch_size = 50
    
    # Process missing in small batches with pause
    for i in range(0, len(missing_tickers), batch_size):
        batch = missing_tickers[i:i+batch_size]
        batch_data = _download_batch(batch, period='60d', interval='1d')
        new_data.update(batch_data)
        if i + batch_size < len(missing_tickers):
            time.sleep(0.3)
        
    # Process updates in small batches
    for i in range(0, len(update_tickers), batch_size):
        batch = update_tickers[i:i+batch_size]
        batch_data = _download_batch(batch, period='5d', interval='1d')
        
        for t, df_update in batch_data.items():
            if t in cache:
                df_old = cache[t]
                combined = pd.concat([df_old, df_update])
                combined = combined[~combined.index.duplicated(keep='last')]
                combined.sort_index(inplace=True)
                new_data[t] = combined
            else:
                new_data[t] = df_update
        if i + batch_size < len(update_tickers):
            time.sleep(0.2)
                
    # Update cache
    final_data = {}
    for t in tickers:
        df = new_data.get(t, cache.get(t))
        if df is not None and not df.empty:
            # Enforce max 60 trading days
            final_data[t] = df.tail(60)
            
    # Save cache
    try:
        with open(_OHLCV_CACHE_FILE, 'wb') as f:
            pickle.dump(final_data, f)
    except Exception as e:
        logger.warning(f"Could not save cache: {e}")
        
    return final_data

def compute_signals(df: pd.DataFrame, symbol: str = '') -> Optional[Dict]:
    """
    Computes technical signals from OHLCV DataFrame.
    Returns None if not enough data (< 20 rows).
    """
    if df is None or len(df) < 20:
        return None
        
    # Create copy to avoid SettingWithCopyWarning
    df = df.copy()
    
    # Volume spike ratio
    df['SMA20_Vol'] = df['Volume'].rolling(window=20).mean()
    curr_vol = df['Volume'].iloc[-1]
    sma_vol = df['SMA20_Vol'].iloc[-1]
    vol_spike = (curr_vol / sma_vol) if sma_vol and sma_vol > 0 else 1.0
    
    # RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI_14'] = 100 - (100 / (1 + rs))
    
    rsi = df['RSI_14'].iloc[-1]
    prev_rsi = df['RSI_14'].iloc[-2] if len(df) > 20 else rsi
    
    # SMA 50 and 200 Reclaim
    df['SMA50'] = df['Close'].rolling(window=50).mean()
    df['SMA200'] = df['Close'].rolling(window=200).mean()
    
    curr_close = df['Close'].iloc[-1]
    prev_close = df['Close'].iloc[-2]
    
    curr_sma50 = df['SMA50'].iloc[-1]
    prev_sma50 = df['SMA50'].iloc[-2]
    sma50_reclaim = (prev_close < prev_sma50) and (curr_close > curr_sma50) if not pd.isna(curr_sma50) else False
    
    curr_sma200 = df['SMA200'].iloc[-1]
    prev_sma200 = df['SMA200'].iloc[-2]
    sma200_reclaim = (prev_close < prev_sma200) and (curr_close > curr_sma200) if not pd.isna(curr_sma200) else False
    
    # Bollinger Bands (20-period, 2 std)
    df['SMA20'] = df['Close'].rolling(window=20).mean()
    df['STD20'] = df['Close'].rolling(window=20).std()
    df['Upper_Band'] = df['SMA20'] + (df['STD20'] * 2)
    df['Lower_Band'] = df['SMA20'] - (df['STD20'] * 2)
    
    upper_band = df['Upper_Band'].iloc[-1]
    lower_band = df['Lower_Band'].iloc[-1]
    sma20 = df['SMA20'].iloc[-1]
    
    bandwidth = (upper_band - lower_band) / sma20 if sma20 and not pd.isna(sma20) else 1.0
    bb_squeeze = bandwidth < 0.08
    bb_breakout = curr_close > upper_band
    
    # Gap percentage
    curr_open = df['Open'].iloc[-1]
    gap_pct = ((curr_open - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
    
    return {
        'symbol': symbol,
        'price': curr_close,
        'volume_spike': vol_spike,
        'rsi_14': rsi,
        'prev_rsi_14': prev_rsi,
        'sma50_reclaim': sma50_reclaim,
        'sma200_reclaim': sma200_reclaim,
        'bb_squeeze': bb_squeeze,
        'bb_breakout': bb_breakout,
        'gap_pct': gap_pct,
        'avg_volume': sma_vol
    }

def score_candidate(signals: Dict) -> float:
    """
    Scoring 0-10 based on signals.
    """
    score = 0.0
    
    # Volume spike
    if signals['volume_spike'] >= 2.0:
        score += 2.0
    elif signals['volume_spike'] >= 1.5:
        score += 1.0
        
    # RSI
    rsi = signals['rsi_14']
    prev_rsi = signals['prev_rsi_14']
    if not pd.isna(rsi):
        if rsi < 30:
            score += 1.5
        if not pd.isna(prev_rsi):
            if prev_rsi < 35 and rsi >= 35:
                score += 2.0
            elif prev_rsi < 50 and rsi >= 50:
                score += 1.5
                
    # SMA Reclaims
    if signals['sma50_reclaim']:
        score += 1.5
    if signals['sma200_reclaim']:
        score += 2.0
        
    # Bollinger Bands
    if signals['bb_breakout']:
        score += 1.5
    if signals['bb_squeeze']:
        score += 0.5
        
    # Gap
    if signals['gap_pct'] >= 3.0 and signals['volume_spike'] >= 1.3:
        score += 1.0
        
    return round(min(score, 10.0), 2)

def apply_filters(
    candidates: List[Dict],
    owned_symbols: Set[str],
    pending_symbols: Set[str],
    cooldown_map: Dict[str, float],
    asset_filter: str = 'all'
) -> List[Dict]:
    """
    Filters candidates based on minimum score, volume, ownership, and cooldowns.
    Returns top SCREENER_MAX_CANDIDATES.
    """
    filtered = []
    
    for c in candidates:
        sym = c['symbol']
        asset_type = c['asset_type']
        
        if asset_filter != 'all' and asset_type != asset_filter:
            continue
            
        if sym in owned_symbols:
            continue
            
        if sym in pending_symbols:
            continue
            
        # Cooldown check
        if sym in cooldown_map:
            import time
            hours_since = (time.time() - cooldown_map[sym]) / 3600.0
            if hours_since < SCREENER_COOLDOWN_HOURS:
                continue
                
        # Volume check
        avg_vol = c['signals']['avg_volume']
        if asset_type == 'stock' and avg_vol < SCREENER_MIN_VOLUME:
            continue
        if asset_type == 'crypto' and avg_vol < 1000:
            continue
            
        # Score check
        score = c['score']
        if asset_type == 'stock' and score < SCREENER_MIN_SCORE_STOCK:
            continue
        if asset_type == 'crypto' and score < SCREENER_MIN_SCORE_CRYPTO:
            continue
            
        filtered.append(c)
        
    # Sort and cap
    filtered.sort(key=lambda x: x['score'], reverse=True)
    return filtered[:SCREENER_MAX_CANDIDATES]

def run_scan(
    asset_filter: str = 'all',
    owned_symbols: Optional[Set[str]] = None,
    pending_symbols: Optional[Set[str]] = None,
    cooldown_map: Optional[Dict[str, float]] = None
) -> Dict:
    """
    Orchestrates full cycle: loads universe, fetches data, computes signals, scores, filters.
    """
    global _latest_scan_status
    
    if owned_symbols is None:
        owned_symbols = set()
    if pending_symbols is None:
        pending_symbols = set()
    if cooldown_map is None:
        cooldown_map = {}
        
    logger.info(f"Starting scan with asset_filter={asset_filter}")
    
    # 1. Load universe
    universe = load_universe()
    if asset_filter != 'all':
        universe = {k: v for k, v in universe.items() if v == asset_filter}
        
    tickers = list(universe.keys())
    
    # 2. Fetch bulk OHLCV
    ohlcv_map = fetch_bulk_ohlcv(tickers)
    
    # 3 & 4. Compute signals and score
    all_scored = []
    for sym, df in ohlcv_map.items():
        if df.empty:
            continue
        signals = compute_signals(df, symbol=sym)
        if signals:
            score = score_candidate(signals)
            asset_type = universe.get(sym, 'stock')
            
            all_scored.append({
                'symbol': sym,
                'asset_type': asset_type,
                'score': score,
                'price': signals['price'],
                'volume_spike': signals['volume_spike'],
                'rsi_14': signals['rsi_14'],
                'signals': signals
            })
            
    all_scored.sort(key=lambda x: x['score'], reverse=True)
    
    # 5. Apply filters
    filtered_candidates = apply_filters(
        all_scored,
        owned_symbols,
        pending_symbols,
        cooldown_map,
        asset_filter
    )
    
    # 6. Return result dict
    result = {
        'tickers_scanned': len(ohlcv_map),
        'candidates': filtered_candidates,
        'all_scored': all_scored[:20],
        'scan_time': datetime.datetime.now().isoformat(),
        'asset_filter': asset_filter,
    }
    
    _latest_scan_status = result
    logger.info(f"Scan complete. Found {len(filtered_candidates)} candidates out of {len(ohlcv_map)} scanned.")
    
    return result

def get_scan_status() -> Dict:
    """
    Returns the latest scan status dict.
    """
    return _latest_scan_status
