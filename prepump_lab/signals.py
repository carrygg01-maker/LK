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


FEATURE_OF = {"oi_expansion": "oi_chg", "fut_cvd": "fut_cvd_z", "spot_cvd": "spot_cvd_z",
              "rel_volume": "rvol", "not_extended": "ret_ext"}


def unknown(f: pd.DataFrame) -> pd.DataFrame:
    """True = คำนวณไม่ได้เพราะข้อมูลใน lookback ขาด (UNKNOWN ไม่ใช่ FALSE)"""
    return pd.DataFrame({c: f[col].isna() for c, col in FEATURE_OF.items()}, index=f.index)


def alert_state(c: pd.DataFrame, u: pd.DataFrame, cfg: Cfg, has_spot: bool = True) -> pd.Series:
    """
    ต่อนาที: 'ALERT' / 'NO' / 'UNKNOWN'
    UNKNOWN = เงื่อนไขที่รู้ค่าผ่านหมด แต่บางข้อคำนวณไม่ได้ -> อาจมี alert ที่เรามองไม่เห็น
    เหรียญที่ไม่มีตลาด spot เลย: Spot CVD = ไม่เกี่ยว (ไม่ใช่ UNKNOWN)
    """
    su = u["spot_cvd"] if has_spot else pd.Series(False, index=u.index)
    if cfg.signal.require_spot_cvd:
        cvd_pass, cvd_unk = c["spot_cvd"], u["spot_cvd"]
    else:
        cvd_pass = c["spot_cvd"] | c["fut_cvd"]
        cvd_unk = ~cvd_pass & (u["fut_cvd"] | su)
    req = [(c["oi_expansion"], u["oi_expansion"]), (cvd_pass, cvd_unk),
           (c["rel_volume"], u["rel_volume"]), (c["not_extended"], u["not_extended"])]
    all_pass = np.logical_and.reduce([p for p, _ in req])
    possible = np.logical_and.reduce([p | k for p, k in req])
    out = np.where(all_pass, "ALERT", np.where(possible, "UNKNOWN", "NO"))
    return pd.Series(out, index=c.index)


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
