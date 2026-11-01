"""
signals.py — กฎสัญญาณ ใช้เฉพาะคอลัมน์จาก features.py (ไม่มีทางเห็น label)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import Cfg

COMPONENTS = ["oi_expansion", "fut_cvd", "spot_cvd", "rel_volume", "not_extended"]

LABELS_TH = {
    "oi_expansion": "OI Expansion",
    "fut_cvd": "Futures CVD",
    "spot_cvd": "Spot CVD",
    "rel_volume": "Relative Volume",
    "not_extended": "Price ยังไม่วิ่ง",
}


def components(f: pd.DataFrame, cfg: Cfg) -> pd.DataFrame:
    s = cfg.signal
    c = pd.DataFrame(index=f.index)
    c["oi_expansion"] = f["oi_chg"] >= s.oi_min_chg
    c["fut_cvd"] = f["fut_cvd_z"] >= s.cvd_z
    c["spot_cvd"] = f["spot_cvd_z"] >= s.cvd_z
    c["rel_volume"] = f["rvol"] >= s.rv_min
    c["not_extended"] = f["ret_ext"] < s.ext_max
    return c.fillna(False).astype(bool)


def raw_alert(c: pd.DataFrame, cfg: Cfg) -> pd.Series:
    cvd = c["spot_cvd"] if cfg.signal.require_spot_cvd else (c["spot_cvd"] | c["fut_cvd"])
    return c["oi_expansion"] & cvd & c["rel_volume"] & c["not_extended"]


def pressure(c: pd.DataFrame) -> pd.Series:
    """0-100 สำหรับกราฟ: OI / CVD / Volume ผ่านกี่ข้อ (ใช้แสดงผล ไม่ได้ใช้ตัดสิน)"""
    core = c["oi_expansion"].astype(int) + c["rel_volume"].astype(int) + (c["fut_cvd"] | c["spot_cvd"]).astype(int)
    return (core / 3 * 100).round()


def alerts(c: pd.DataFrame, cfg: Cfg) -> pd.DatetimeIndex:
    """alert หลัง cooldown — เดินตามเวลา จึงไม่มีทางรู้อนาคต"""
    hit = raw_alert(c, cfg)
    ts = hit.index[hit.values]
    kept, last = [], None
    cd = pd.Timedelta(minutes=cfg.signal.cooldown)
    for t in ts:
        if last is None or t - last >= cd:
            kept.append(t)
            last = t
    return pd.DatetimeIndex(kept)
