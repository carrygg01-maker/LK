"""
fetch.py — ดึงข้อมูลจริงจาก Binance Vision (ฟรี) + REST API สำหรับวันที่ Vision ยังไม่ออกไฟล์

โครงสร้างไฟล์ที่ได้ (parquet ต่อเหรียญต่อ dataset):
    data/<SYMBOL>/fut_kl.parquet     futures klines 1m
    data/<SYMBOL>/spot_kl.parquet    spot klines 1m (ถ้ามีตลาด spot)
    data/<SYMBOL>/metrics.parquet    OI 5m + long/short ratio (Vision metrics)
    data/<SYMBOL>/funding.parquet    funding rate
    data/<SYMBOL>/fut_agg1m.parquet  futures aggTrades สรุปเป็น 1m (ถ้าสั่ง --aggtrades)
    data/<SYMBOL>/spot_agg1m.parquet spot aggTrades สรุปเป็น 1m

ทุก timestamp เก็บเป็น UTC
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

VISION = "https://data.binance.vision/data"
VISION_S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FAPI = "https://fapi.binance.com"
SAPI = "https://api.binance.com"

ROOT = Path(__file__).parent
DATA = ROOT / "data"
RAW = ROOT / "data" / "_raw"

KL_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
           "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


# ------------------------------------------------------------------ http utils

def _http(url: str, retries: int = 4, binary: bool = False):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "prepump-lab/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
                return b if binary else json.loads(b.decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (418, 429):
                time.sleep(15 * (i + 1))
                continue
            if i == retries - 1:
                raise
        except Exception:  # noqa: BLE001
            if i == retries - 1:
                raise
        time.sleep(2 ** (i + 1))
    return None


def _to_dt(x: pd.Series) -> pd.Series:
    """Vision spot เปลี่ยนเป็น microseconds ตั้งแต่ 2025 — ตรวจหน่วยอัตโนมัติ"""
    x = pd.to_numeric(x)
    unit = "us" if x.max() > 1e14 else "ms"
    return pd.to_datetime(x, unit=unit, utc=True)


def _read_zip_csv(blob: bytes, names: list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read(z.namelist()[0])
    first = raw[:200].decode(errors="ignore").split("\n")[0]
    has_header = not first[:1].isdigit()
    df = pd.read_csv(io.BytesIO(raw), header=0 if has_header else None)
    if not has_header:
        df.columns = names[: df.shape[1]]
    return df


def _days(start: str, end: str) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, end, freq="D")]


def _vision_daily(rel: str, names: list[str]) -> pd.DataFrame | None:
    """rel เช่น 'futures/um/daily/metrics/XUSDT/XUSDT-metrics-2026-10-25.zip'"""
    url = f"{VISION}/{rel}"
    cache = RAW / rel
    if cache.exists():
        blob = cache.read_bytes()
    else:
        blob = _http(url, binary=True)
        if blob is None:
            return None
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(blob)
    return _read_zip_csv(blob, names)


# ------------------------------------------------------------------- klines

def _norm_klines(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ts"] = _to_dt(df["open_time"])
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_base", "taker_buy_quote", "trades"]:
        df[c] = pd.to_numeric(df[c])
    df = df[["ts", "open", "high", "low", "close", "volume", "quote_volume",
             "trades", "taker_buy_base", "taker_buy_quote"]]
    return df.set_index("ts").sort_index()


def _rest_klines(sym: str, market: str, t0: pd.Timestamp, t1: pd.Timestamp) -> pd.DataFrame:
    base, path = (FAPI, "/fapi/v1/klines") if market == "fut" else (SAPI, "/api/v3/klines")
    out, cur = [], int(t0.timestamp() * 1000)
    end = int(t1.timestamp() * 1000)
    while cur < end:
        q = urllib.parse.urlencode({"symbol": sym, "interval": "1m", "startTime": cur,
                                    "endTime": end, "limit": 1000 if market == "spot" else 1500})
        b = _http(f"{base}{path}?{q}")
        if not b:
            break
        out.extend(b)
        cur = b[-1][0] + 60_000
        time.sleep(0.15)
    if not out:
        return pd.DataFrame()
    return _norm_klines(pd.DataFrame(out, columns=KL_COLS))


def klines(sym: str, market: str, start: str, end: str) -> pd.DataFrame:
    base = "futures/um/daily/klines" if market == "fut" else "spot/daily/klines"
    parts, missing = [], []
    for d in _days(start, end):
        df = _vision_daily(f"{base}/{sym}/1m/{sym}-1m-{d}.zip", KL_COLS)
        if df is None:
            missing.append(d)
        else:
            parts.append(_norm_klines(df))
    for d in missing:  # Vision ออกไฟล์ช้า ~1 วัน -> เติมจาก REST
        t0 = pd.Timestamp(d, tz="UTC")
        try:
            r = _rest_klines(sym, market, t0, t0 + pd.Timedelta(days=1) - pd.Timedelta(minutes=1))
        except Exception as e:  # noqa: BLE001
            print(f"    REST {market} klines {d} ล้มเหลว: {e}")
            r = pd.DataFrame()
        if len(r):
            parts.append(r)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts).sort_index()
    return out[~out.index.duplicated()]


# ------------------------------------------------------------------ metrics/OI

MET_COLS = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
            "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
            "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]


def metrics(sym: str, start: str, end: str) -> pd.DataFrame:
    parts, missing = [], []
    for d in _days(start, end):
        df = _vision_daily(f"futures/um/daily/metrics/{sym}/{sym}-metrics-{d}.zip", MET_COLS)
        if df is None:
            missing.append(d)
            continue
        df["ts"] = pd.to_datetime(df["create_time"], utc=True)
        parts.append(df.set_index("ts")[["sum_open_interest", "sum_open_interest_value",
                                         "sum_toptrader_long_short_ratio",
                                         "sum_taker_long_short_vol_ratio"]])
    if missing:
        # REST ให้ย้อนหลังแค่ ~30 วัน — ถ้าเลยแล้วจะไม่มีข้อมูลส่วนนี้
        try:
            t0 = int(pd.Timestamp(missing[0], tz="UTC").timestamp() * 1000)
            t1 = int((pd.Timestamp(missing[-1], tz="UTC") + pd.Timedelta(days=1)).timestamp() * 1000)
            rows, cur = [], t0
            while cur < t1:
                q = urllib.parse.urlencode({"symbol": sym, "period": "5m", "startTime": cur,
                                            "endTime": t1, "limit": 500})
                b = _http(f"{FAPI}/futures/data/openInterestHist?{q}")
                if not b:
                    break
                rows.extend(b)
                cur = b[-1]["timestamp"] + 300_000
                time.sleep(0.2)
            if rows:
                r = pd.DataFrame(rows)
                r["ts"] = pd.to_datetime(r["timestamp"], unit="ms", utc=True)
                r["sum_open_interest"] = pd.to_numeric(r["sumOpenInterest"])
                r["sum_open_interest_value"] = pd.to_numeric(r["sumOpenInterestValue"])
                parts.append(r.set_index("ts")[["sum_open_interest", "sum_open_interest_value"]])
        except Exception as e:  # noqa: BLE001
            print(f"    REST OI ล้มเหลว: {e}")
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts).sort_index()
    return out[~out.index.duplicated()]


def funding(sym: str, start: str, end: str) -> pd.DataFrame:
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    t1 = int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp() * 1000)
    out, cur = [], t0
    try:
        while cur < t1:
            q = urllib.parse.urlencode({"symbol": sym, "startTime": cur, "endTime": t1, "limit": 1000})
            b = _http(f"{FAPI}/fapi/v1/fundingRate?{q}")
            if not b:
                break
            out.extend(b)
            cur = b[-1]["fundingTime"] + 1
            if len(b) < 1000:
                break
    except Exception as e:  # noqa: BLE001
        print(f"    REST funding ล้มเหลว: {e}")
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding"] = pd.to_numeric(df["fundingRate"])
    return df.set_index("ts")[["funding"]].sort_index()


# ----------------------------------------------------------------- aggTrades

FUT_AGG = ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
           "transact_time", "is_buyer_maker"]
SPOT_AGG = FUT_AGG + ["is_best_match"]


def _agg_to_1m(df: pd.DataFrame, big_usd: float) -> pd.DataFrame:
    ts = _to_dt(df["transact_time"])
    px = pd.to_numeric(df["price"])
    usd = px * pd.to_numeric(df["quantity"])
    maker = df["is_buyer_maker"].astype(str).str.lower().isin(["true", "1"])
    buy = np.where(~maker, usd, 0.0)
    sell = np.where(maker, usd, 0.0)
    big = usd >= big_usd
    g = pd.DataFrame({"ts": ts.dt.floor("1min"), "buy_usd": buy, "sell_usd": sell,
                      "big_buy_usd": np.where(big & ~maker, usd, 0.0),
                      "big_sell_usd": np.where(big & maker, usd, 0.0),
                      "n_trades": 1, "max_trade_usd": usd})
    return g.groupby("ts").agg({"buy_usd": "sum", "sell_usd": "sum", "big_buy_usd": "sum",
                                "big_sell_usd": "sum", "n_trades": "sum", "max_trade_usd": "max"})


def aggtrades_1m(sym: str, market: str, start: str, end: str, big_usd: float) -> pd.DataFrame:
    base = "futures/um/daily/aggTrades" if market == "fut" else "spot/daily/aggTrades"
    names = FUT_AGG if market == "fut" else SPOT_AGG
    parts = []
    for d in _days(start, end):
        df = _vision_daily(f"{base}/{sym}/{sym}-aggTrades-{d}.zip", names)
        if df is None:
            print(f"    {market} aggTrades {d}: ยังไม่มีใน Vision (ข้าม — REST aggTrades ช้าเกินไปสำหรับย้อนหลัง)")
            continue
        parts.append(_agg_to_1m(df, big_usd))
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts).sort_index()
    return out[~out.index.duplicated()]


# ---------------------------------------------------------------- universe

def list_vision_symbols(prefix: str = "data/futures/um/daily/klines/") -> list[str]:
    """
    รายชื่อเหรียญทั้งหมดที่ Vision เคยมี — รวมเหรียญที่ delist ไปแล้ว
    (exchangeInfo ให้แค่เหรียญที่ยังเทรดอยู่ = survivorship bias)
    """
    syms, marker = [], ""
    while True:
        q = urllib.parse.urlencode({"prefix": prefix, "delimiter": "/", "marker": marker})
        blob = _http(f"{VISION_S3}?{q}", binary=True)
        if blob is None:
            break
        root = ET.fromstring(blob)
        ns = {"s": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}
        pre = "s:" if ns else ""
        for cp in root.findall(f"{pre}CommonPrefixes", ns):
            p = cp.find(f"{pre}Prefix", ns).text
            syms.append(p.rstrip("/").split("/")[-1])
        trunc = root.find(f"{pre}IsTruncated", ns)
        if trunc is None or trunc.text != "true" or not syms:
            break
        marker = prefix + syms[-1] + "/"
    return sorted(s for s in set(syms) if s.endswith("USDT"))


# --------------------------------------------------------------------- main

def fetch_symbol(sym: str, start: str, end: str, aggtrades: bool = False,
                 big_usd: float = 20_000) -> dict:
    out_dir = DATA / sym
    out_dir.mkdir(parents=True, exist_ok=True)
    got = {}
    jobs = [("fut_kl", lambda: klines(sym, "fut", start, end)),
            ("spot_kl", lambda: klines(sym, "spot", start, end)),
            ("metrics", lambda: metrics(sym, start, end)),
            ("funding", lambda: funding(sym, start, end))]
    if aggtrades:
        jobs += [("fut_agg1m", lambda: aggtrades_1m(sym, "fut", start, end, big_usd)),
                 ("spot_agg1m", lambda: aggtrades_1m(sym, "spot", start, end, big_usd))]
    for name, fn in jobs:
        print(f"  {sym} {name} ...", flush=True)
        df = fn()
        if len(df):
            df.to_parquet(out_dir / f"{name}.parquet")
        got[name] = len(df)
        print(f"    -> {len(df):,} แถว")
    (out_dir / "meta.json").write_text(json.dumps({"symbol": sym, "start": start, "end": end,
                                                   "rows": got}, indent=2))
    return got


def load_symbol(sym: str, data_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    d = (data_dir or DATA) / sym
    out = {}
    for name in ["fut_kl", "spot_kl", "metrics", "funding", "fut_agg1m", "spot_agg1m"]:
        f = d / f"{name}.parquet"
        out[name] = pd.read_parquet(f) if f.exists() else pd.DataFrame()
    return out
