"""
synthetic.py — ข้อมูลปลอมที่ "รู้คำตอบล่วงหน้า" ใช้ตรวจว่าเครื่องมือวัดถูก

ฝังไว้ 3 แบบ:
  real    : OI/CVD/Volume สะสมก่อน แล้วราคาพุ่ง      -> ควรจับได้ lead ≈ build_min
  decoy   : OI/CVD/Volume สะสมเหมือนกัน แต่ราคาไม่ไปไหน -> ควรเป็น false alert
  surprise: ราคาพุ่งเฉยๆ ไม่มีอะไรนำ                 -> ควรจับไม่ได้ (late หรือไม่มีเลย)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def make(days: int = 14, seed: int = 1, start: str = "2026-10-18",
         real: list[tuple[float, int]] | None = None,
         decoy: list[float] | None = None,
         surprise: list[float] | None = None,
         build_min: int = 90, edge: bool = True) -> tuple[dict, dict]:
    """
    real: [(day_offset, build_min), ...]  edge=False -> ไม่ฝังอะไรเลย (random walk ล้วน)
    คืน (raw dict แบบเดียวกับ fetch.load_symbol, truth)
    """
    rng = np.random.default_rng(seed)
    n = days * 1440
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    r = rng.normal(0, 0.0012, n)
    qv = rng.lognormal(np.log(20_000), 0.6, n)
    buy_share = np.clip(0.5 + rng.normal(0, 0.06, n), 0.05, 0.95)
    oi_step = rng.normal(0, 0.0004, n)
    spot_qv = qv * 0.4 * rng.lognormal(0, 0.3, n)
    spot_share = np.clip(0.5 + rng.normal(0, 0.06, n), 0.05, 0.95)

    real = real if real is not None else ([(9.4, build_min)] if edge else [])
    decoy = decoy if decoy is not None else ([3.3, 6.6] if edge else [])
    surprise = surprise if surprise is not None else ([12.2] if edge else [])
    truth = {"real": [], "decoy": [], "surprise": []}
    ok = lambda d: 1.5 <= d <= days - 0.5  # noqa: E731  ต้องมี warm-up และไม่เลยข้อมูล
    real = [x for x in real if ok(x[0])]
    decoy = [x for x in decoy if ok(x)]
    surprise = [x for x in surprise if ok(x)]

    def build(i0, L):
        sl = slice(i0 - L, i0)
        oi_step[sl] += 0.12 / L                         # OI +~12% ระหว่างสะสม
        qv[sl] *= 4.0
        buy_share[sl] = np.clip(buy_share[sl] + 0.15, 0, 0.95)
        spot_qv[sl] *= 4.0
        spot_share[sl] = np.clip(spot_share[sl] + 0.15, 0, 0.95)
        r[sl] = rng.normal(0, 0.0008, L)                 # ราคาเงียบ

    def pump(i0, L=360, total=0.45):
        r[i0:i0 + L] += np.log1p(total) / L
        qv[i0:i0 + L] *= 6

    for d, L in real:
        i0 = int(d * 1440)
        build(i0, L)
        pump(i0)
        truth["real"].append({"pump_start": str(idx[i0]), "build_min": L})
    for d in decoy:
        i0 = int(d * 1440)
        build(i0, build_min)
        truth["decoy"].append(str(idx[i0]))
    for d in surprise:
        i0 = int(d * 1440)
        pump(i0)
        truth["surprise"].append(str(idx[i0]))

    close = 0.10 * np.exp(np.cumsum(r))
    op = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 0.0006, n))
    high = np.maximum(op, close) * (1 + wick)
    low = np.minimum(op, close) * (1 - wick)
    fut = pd.DataFrame({"open": op, "high": high, "low": low, "close": close,
                        "volume": qv / close, "quote_volume": qv, "trades": 100,
                        "taker_buy_base": qv * buy_share / close,
                        "taker_buy_quote": qv * buy_share}, index=idx)
    spot = pd.DataFrame({"open": op, "high": high, "low": low, "close": close,
                         "volume": spot_qv / close, "quote_volume": spot_qv, "trades": 50,
                         "taker_buy_base": spot_qv * spot_share / close,
                         "taker_buy_quote": spot_qv * spot_share}, index=idx)
    oi = 5e6 * np.exp(np.cumsum(oi_step))
    oi5 = pd.Series(oi, index=idx).iloc[::5]
    met = pd.DataFrame({"sum_open_interest": oi5.values,
                        "sum_open_interest_value": oi5.values * close[::5]}, index=oi5.index)
    fidx = pd.date_range(start, periods=days * 3, freq="8h", tz="UTC")
    fund = pd.DataFrame({"funding": rng.normal(0.0001, 0.0002, len(fidx))}, index=fidx)
    raw = {"fut_kl": fut, "spot_kl": spot, "metrics": met, "funding": fund,
           "fut_agg1m": pd.DataFrame(), "spot_agg1m": pd.DataFrame()}
    return raw, truth


def universe(n_symbols: int = 30, months: int = 5, seed: int = 100, edge: bool = True) -> dict:
    """หลายเหรียญสำหรับทดสอบ walk-forward: ส่วนหนึ่งมี pump จริง ส่วนหนึ่งไม่มีอะไรเลย"""
    rng = np.random.default_rng(seed)
    days = months * 30
    out = {}
    for k in range(n_symbols):
        n_real = int(rng.integers(0, 4)) if edge else 0
        n_decoy = int(rng.integers(0, 4)) if edge else 0
        real = [(float(rng.uniform(2, days - 2)), int(rng.integers(45, 180))) for _ in range(n_real)]
        decoy = [float(rng.uniform(2, days - 2)) for _ in range(n_decoy)]
        raw, _ = make(days=days, seed=seed + k, start="2026-03-01", real=real, decoy=decoy,
                      surprise=[], edge=edge)
        out[f"SYN{k:03d}USDT"] = raw
    return out
