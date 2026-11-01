"""
labels.py — นิยาม "พุ่ง" สำหรับการประเมินผลเท่านั้น

ไฟล์นี้ "ใช้ข้อมูลอนาคต" โดยตั้งใจ (centered window) เพราะเป็นกระดาษคำตอบ
signals.py / features.py ห้าม import ไฟล์นี้ — test_no_lookahead ตรวจให้
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import Cfg


@dataclass
class Episode:
    low_time: pd.Timestamp
    low: float
    breakout_time: pd.Timestamp
    peak_time: pd.Timestamp
    peak: float

    @property
    def gain(self) -> float:
        return self.peak / self.low - 1

    def as_dict(self) -> dict:
        return {"low_time": str(self.low_time), "low": self.low,
                "breakout_time": str(self.breakout_time), "peak_time": str(self.peak_time),
                "peak": self.peak, "gain_pct": round(self.gain * 100, 2)}


def episodes(b: pd.DataFrame, cfg: Cfg) -> list[Episode]:
    """
    เดินตามเวลา: หา t0 แรกที่ max(high ใน W นาทีข้างหน้า) / low[t0] - 1 >= pump_gain
    peak = จุดสูงสุดใน [t0, t0+W], low = จุดต่ำสุดใน [t0, peak], breakout = close แรก >= low*(1+B)
    แล้วค้นต่อหลัง peak
    """
    L = cfg.label
    W = L.pump_window
    hi = b["high"].to_numpy()
    lo = b["low"].to_numpy()
    cl = b["close"].to_numpy()
    idx = b.index
    fmax = pd.Series(hi[::-1]).rolling(W + 1, min_periods=1).max().to_numpy()[::-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        gain = fmax / lo - 1
    q = np.flatnonzero(gain >= L.pump_gain)
    out: list[Episode] = []
    i = 0
    while i < len(q):
        t0 = q[i]
        end = min(t0 + W, len(hi) - 1)
        p = t0 + int(np.argmax(hi[t0:end + 1]))
        while True:  # ขยาย peak ถ้ายังทำ high ใหม่ภายใน W นาทีถัดไป
            e2 = min(p + W, len(hi) - 1)
            p2 = p + int(np.argmax(hi[p:e2 + 1]))
            if p2 == p:
                break
            p = p2
        lt = t0 + int(np.argmin(lo[t0:p + 1]))
        low = lo[lt]
        br = np.flatnonzero(cl[lt:p + 1] >= low * (1 + L.breakout_gain))
        if len(br) and hi[p] / low - 1 >= L.pump_gain:
            out.append(Episode(idx[lt], float(low), idx[lt + br[0]], idx[p], float(hi[p])))
        nxt = np.searchsorted(q, p + 1)
        i = max(nxt, i + 1)
    return out


def classify(alerts: pd.DatetimeIndex, eps: list[Episode], cfg: Cfg) -> pd.DataFrame:
    """lead = ก่อน breakout ภายใน lead_window, late = ระหว่าง breakout..peak, false = อื่นๆ"""
    lw = pd.Timedelta(minutes=cfg.label.lead_window)
    rows = []
    for t in alerts:
        kind, ep_i, lead = "false", None, np.nan
        for i, e in enumerate(eps):
            if e.breakout_time - lw <= t < e.breakout_time:
                kind, ep_i = "lead", i
                lead = (e.breakout_time - t).total_seconds() / 60
                break
            if e.breakout_time <= t <= e.peak_time:
                kind, ep_i = "late", i
                lead = -(t - e.breakout_time).total_seconds() / 60
                break
        rows.append({"time": t, "kind": kind, "episode": ep_i, "lead_min": lead})
    return pd.DataFrame(rows, columns=["time", "kind", "episode", "lead_min"])
