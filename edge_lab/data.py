"""
data.py — Binance USD-M Futures data layer (no API key needed, public endpoints only)

ทุกอย่าง cache ลง ./cache/ เป็น parquet ดึงครั้งเดียวแล้วใช้ซ้ำได้
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

FAPI = "https://fapi.binance.com"
SAPI = "https://api.binance.com"
CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(exist_ok=True)

_LAST_CALL = [0.0]
MIN_INTERVAL = 0.12  # เว้นจังหวะกัน rate limit (2400 weight/min)


def _get(base: str, path: str, params: dict | None = None, retries: int = 5):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        gap = time.time() - _LAST_CALL[0]
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        _LAST_CALL[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "edge-lab/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code in (418, 429):
                wait = 10 * (attempt + 1)
                print(f"  rate limited, รอ {wait}s ...")
                time.sleep(wait)
                continue
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------- exchange info

def perp_symbols(quote: str = "USDT") -> pd.DataFrame:
    """perpetual ที่ยัง TRADING อยู่ พร้อม onboardDate (= วันที่ list)"""
    f = CACHE / "exchange_info.parquet"
    if f.exists() and (time.time() - f.stat().st_mtime) < 86400:
        return pd.read_parquet(f)
    info = _get(FAPI, "/fapi/v1/exchangeInfo")
    rows = []
    for s in info["symbols"]:
        if s["contractType"] != "PERPETUAL" or s["quoteAsset"] != quote:
            continue
        if s["status"] != "TRADING":
            continue
        rows.append(
            {
                "symbol": s["symbol"],
                "base": s["baseAsset"],
                "onboard": pd.to_datetime(s["onboardDate"], unit="ms", utc=True),
            }
        )
    df = pd.DataFrame(rows).sort_values("onboard").reset_index(drop=True)
    df.to_parquet(f)
    return df


# ---------------------------------------------------------------------- klines

_KL_COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]


def klines(symbol: str, interval: str = "1h", start: str = "2020-01-01",
           end: str | None = None, market: str = "futures") -> pd.DataFrame:
    """
    OHLCV + taker buy volume (ใช้ทำ CVD ได้)
    market: "futures" (USD-M perp) หรือ "spot"
    """
    key = f"kl_{market}_{symbol}_{interval}_{start}".replace("-", "")
    f = CACHE / f"{key}.parquet"
    if f.exists():
        return pd.read_parquet(f)

    base = FAPI if market == "futures" else SAPI
    path = "/fapi/v1/klines" if market == "futures" else "/api/v3/klines"
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    t1 = int((pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.utcnow().tz_localize("UTC")).timestamp() * 1000)

    out = []
    cur = t0
    while cur < t1:
        batch = _get(base, path, {"symbol": symbol, "interval": interval,
                                  "startTime": cur, "limit": 1500})
        if not batch:
            break
        out.extend(batch)
        nxt = batch[-1][0] + 1
        if nxt <= cur:
            break
        cur = nxt
        if len(batch) < 1500:
            break

    if not out:
        return pd.DataFrame()

    df = pd.DataFrame(out, columns=_KL_COLS)
    df = df.drop(columns=["ignore", "close_time"])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_base", "taker_buy_quote"]:
        df[c] = pd.to_numeric(df[c])
    df["trades"] = pd.to_numeric(df["trades"])
    df = df.drop(columns=["open_time"]).set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.to_parquet(f)
    return df


# --------------------------------------------------------------- funding rate

def funding(symbol: str, start: str = "2020-01-01") -> pd.DataFrame:
    """ประวัติ funding rate ย้อนได้ถึงวันที่เหรียญ list — ข้อมูลยาว ใช้ได้จริง"""
    f = CACHE / f"fund_{symbol}_{start.replace('-', '')}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    out, cur = [], t0
    while True:
        batch = _get(FAPI, "/fapi/v1/fundingRate",
                     {"symbol": symbol, "startTime": cur, "limit": 1000})
        if not batch:
            break
        out.extend(batch)
        nxt = batch[-1]["fundingTime"] + 1
        if nxt <= cur:
            break
        cur = nxt
        if len(batch) < 1000:
            break
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["rate"] = pd.to_numeric(df["fundingRate"])
    df = df[["ts", "rate"]].set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.to_parquet(f)
    return df


# ------------------------------------------------ open interest (30 วันเท่านั้น)

def open_interest(symbol: str, period: str = "1h") -> pd.DataFrame:
    """
    Binance ให้ย้อนหลังแค่ ~30 วัน — นี่คือเหตุผลว่าทำไมต้องเริ่มเก็บเองตั้งแต่วันนี้
    (ดู collector.py)
    """
    out = _get(FAPI, "/futures/data/openInterestHist",
               {"symbol": symbol, "period": period, "limit": 500})
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["oi"] = pd.to_numeric(df["sumOpenInterest"])
    df["oi_value"] = pd.to_numeric(df["sumOpenInterestValue"])
    return df[["ts", "oi", "oi_value"]].set_index("ts").sort_index()
