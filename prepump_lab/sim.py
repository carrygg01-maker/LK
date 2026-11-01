"""
sim.py — จำลองการเข้าเทรดตามสัญญาณ แบบที่ทำได้จริง

- alert ตัดสินที่ปิดแท่ง t -> เข้า "open ของแท่ง t+1" + slippage (ไม่ใช่ close ของแท่งที่เห็นสัญญาณ)
- ถ้า TP และ SL อยู่ในแท่งเดียวกัน ถือว่าโดน SL ก่อน (สมมติร้ายสุด)
- หักค่าธรรมเนียม + slippage ทั้งขาเข้าและขาออก
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import Cfg


def trade(b: pd.DataFrame, t: pd.Timestamp, cfg: Cfg) -> dict | None:
    T = cfg.trade
    pos = b.index.get_indexer([t])[0]
    if pos < 0 or pos + 1 >= len(b):
        return None
    entry_i = pos + 1
    entry = b["open"].iat[entry_i] * (1 + T.slip_per_side)
    tp = entry * (1 + T.take_profit)
    sl = entry * (1 - T.stop_loss)
    end_i = min(entry_i + T.max_hold, len(b) - 1)
    hi = b["high"].values[entry_i:end_i + 1]
    lo = b["low"].values[entry_i:end_i + 1]
    exit_px, reason, k = None, "time", end_i - entry_i
    for j in range(len(hi)):
        if lo[j] <= sl:
            exit_px, reason, k = sl, "stop", j
            break
        if hi[j] >= tp:
            exit_px, reason, k = tp, "target", j
            break
    if exit_px is None:
        exit_px = b["close"].iat[end_i]
        if end_i == len(b) - 1 and k < T.max_hold:
            reason = "data_end"
    exit_net = exit_px * (1 - T.slip_per_side)
    gross = exit_net / entry - 1
    net = gross - 2 * T.fee_per_side
    mae = lo[: k + 1].min() / entry - 1
    mfe = hi[: k + 1].max() / entry - 1
    return {"alert": t, "entry_time": b.index[entry_i], "entry": entry,
            "exit_time": b.index[entry_i + k], "exit": exit_net, "reason": reason,
            "net_pct": net * 100, "mfe_pct": mfe * 100, "mae_pct": mae * 100,
            "hold_min": k + 1}


def run(b: pd.DataFrame, alerts: pd.DatetimeIndex, cfg: Cfg, no_overlap: bool = True) -> pd.DataFrame:
    rows, busy_until = [], None
    for t in alerts:
        if no_overlap and busy_until is not None and t < busy_until:
            continue
        r = trade(b, t, cfg)
        if r is None:
            continue
        rows.append(r)
        busy_until = r["exit_time"]
    return pd.DataFrame(rows)


def summarize(trades: pd.DataFrame) -> dict:
    if trades is None or not len(trades):
        return {"n": 0}
    x = trades["net_pct"].values / 100
    eq = np.cumprod(1 + x)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    wins, losses = x[x > 0], x[x <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    pf = float(gross_win / gross_loss) if gross_loss > 0 else float("inf")
    avg_w = float(wins.mean() * 100) if len(wins) else 0.0
    avg_l = float(losses.mean() * 100) if len(losses) else 0.0
    return {"n": int(len(x)), "win_rate": float((x > 0).mean()),
            "mean_net_pct": float(x.mean() * 100),           # = expectancy ต่อไม้ หลังต้นทุน
            "expectancy_pct": float(x.mean() * 100),
            "profit_factor": pf, "avg_win_pct": avg_w, "avg_loss_pct": avg_l,
            "payoff_ratio": float(avg_w / -avg_l) if avg_l < 0 else float("inf"),
            "breakeven_win_rate": float(-avg_l / (avg_w - avg_l)) if avg_w > 0 and avg_l < 0 else None,
            "median_net_pct": float(np.median(x) * 100),
            "total_compound_pct": float((eq[-1] - 1) * 100), "max_dd_pct": float(dd * 100),
            "best_pct": float(x.max() * 100), "worst_pct": float(x.min() * 100)}
