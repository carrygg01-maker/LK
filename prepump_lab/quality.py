"""
quality.py — data_quality_report.json

เป้าหมาย: แยก "สูตรไม่มีสัญญาณ" ออกจาก "ข้อมูลไม่พอให้สูตรเห็นสัญญาณ"
ทุกคำสั่ง (fetch / forensic / backtest) เขียนรายงานนี้ทุกครั้ง
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

# dataset -> (ความถี่ที่คาดหวัง, จำเป็นต่อสูตรไหม)
EXPECT = {
    "fut_kl": ("1min", True),
    "spot_kl": ("1min", False),    # บางเหรียญไม่มี spot -> Spot CVD ใช้ไม่ได้ แต่ Futures CVD ยังใช้ได้
    "metrics": ("5min", True),     # OI
    "funding": (None, False),
    "fut_agg1m": ("1min", False),
    "spot_agg1m": ("1min", False),
}
READY_MIN_COVERAGE = 0.98          # klines / OI ต้องครบ >= 98% ของช่วง
GAP_REPORT_MIN = {"1min": 5, "5min": 15}   # gap สั้นกว่านี้ไม่รายงานรายตัว


def _gaps(idx: pd.DatetimeIndex, freq: str, top: int = 10) -> list[dict]:
    if len(idx) < 2:
        return []
    d = pd.Series(idx[1:] - idx[:-1], index=idx[1:])
    step = pd.Timedelta(freq)
    big = d[d > step * (GAP_REPORT_MIN[freq] / (step / pd.Timedelta("1min")))]
    big = big.sort_values(ascending=False).head(top)
    return [{"after": str(t - g), "before": str(t), "missing_min": round(g / pd.Timedelta("1min") - step / pd.Timedelta("1min"))}
            for t, g in big.items()]


def dataset_quality(df: pd.DataFrame | None, freq: str | None, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    if df is None or not len(df):
        return {"rows": 0, "coverage": 0.0, "first": None, "last": None, "gaps": []}
    sub = df.loc[start:end]
    out = {"rows": int(len(sub)), "first": str(df.index[0]), "last": str(df.index[-1])}
    if freq:
        expected = int((end - start) / pd.Timedelta(freq)) + 1
        out["expected"] = expected
        out["coverage"] = round(min(1.0, len(sub.index.unique()) / max(expected, 1)), 4)
        out["gaps"] = _gaps(sub.index, freq)
        if freq == "1min" and "quote_volume" in sub:
            out["zero_volume_share"] = round(float((sub["quote_volume"] == 0).mean()), 4)
    else:
        out["coverage"] = 1.0 if len(sub) else 0.0
        out["gaps"] = []
    return out


def symbol_quality(sym: str, raw: dict[str, pd.DataFrame], start=None, end=None) -> dict:
    fk = raw.get("fut_kl")
    if fk is None or not len(fk):
        return {"symbol": sym, "status": "not_ready", "reasons": ["ไม่มี futures klines"], "datasets": {}}
    s = pd.Timestamp(start, tz="UTC") if start else fk.index[0]
    e = (pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)) if end else fk.index[-1]
    ds = {k: dataset_quality(raw.get(k), f, s, e) for k, (f, _) in EXPECT.items()}
    reasons, warnings = [], []
    for k, (_, req) in EXPECT.items():
        cov = ds[k]["coverage"]
        if req and cov < READY_MIN_COVERAGE:
            reasons.append(f"{k} ครอบคลุม {cov:.1%} < {READY_MIN_COVERAGE:.0%}")
        elif not req and k in ("spot_kl",) and cov < READY_MIN_COVERAGE:
            warnings.append(f"{k} ครอบคลุม {cov:.1%} -> Spot CVD ใช้ได้ไม่เต็ม")
    if ds["fut_kl"].get("zero_volume_share", 0) > 0.2:
        warnings.append(f"แท่ง volume=0 {ds['fut_kl']['zero_volume_share']:.0%} -> สภาพคล่องต่ำ / ข้อมูลแปลก")
    status = "ready" if not reasons else ("partial" if ds["fut_kl"]["coverage"] >= READY_MIN_COVERAGE else "not_ready")
    return {"symbol": sym, "window": [str(s), str(e)], "status": status,
            "reasons": reasons, "warnings": warnings, "datasets": ds}


def window_coverage(b: pd.DataFrame, t0: pd.Timestamp, t1: pd.Timestamp) -> dict:
    """สัดส่วนนาทีที่มีค่าใช้ได้ของแต่ละ feature ในช่วงก่อน breakout (หลังจัดแนวเวลาแล้ว)"""
    w = b.loc[t0:t1]
    if not len(w):
        return {}
    cols = {"OI": "oi_chg", "Futures CVD": "fut_cvd_z", "Spot CVD": "spot_cvd_z", "Relative Volume": "rvol"}
    return {k: round(float(w[c].notna().mean()), 4) for k, c in cols.items()}


def write(reports: list[dict], path: Path) -> Path:
    summary = {"ready": sum(r["status"] == "ready" for r in reports),
               "partial": sum(r["status"] == "partial" for r in reports),
               "not_ready": sum(r["status"] == "not_ready" for r in reports)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated": str(pd.Timestamp.now(tz="UTC")), "summary": summary,
                                "symbols": reports}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path
