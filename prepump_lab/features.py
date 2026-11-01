"""
features.py — ตาราง 1 นาทีแบบ point-in-time

หัวใจของการ "ไม่หลอกตัวเอง":
  แถวที่ index = t (open_time ของแท่ง) หมายถึง "การตัดสินใจ ณ เวลา t + 1 นาที"
  (แท่ง t ปิดที่ t+1m) ทุกค่าในแถวนั้นต้องคำนวณได้จากข้อมูลที่ "เผยแพร่แล้ว" ณ t+1m เท่านั้น

  - kline / aggTrades bucket t      -> เผยแพร่ที่ t + 1m
  - OI metrics create_time c        -> เผยแพร่ที่ c + oi_lag_min (สมมติแบบระวัง)
  - funding fundingTime f           -> เผยแพร่ที่ f

ฟังก์ชัน `visible_raw(raw, asof)` ตัดข้อมูลดิบตามเวลาเผยแพร่ — Replay และ test_no_lookahead ใช้ตัวเดียวกัน
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import Cfg

ONE_MIN = pd.Timedelta(minutes=1)


def publish_time(name: str, idx: pd.DatetimeIndex, cfg: Cfg) -> pd.DatetimeIndex:
    if name in ("fut_kl", "spot_kl", "fut_agg1m", "spot_agg1m"):
        return idx + ONE_MIN
    if name == "metrics":
        return idx + pd.Timedelta(minutes=cfg.data.oi_lag_min)
    return idx  # funding


def visible_raw(raw: dict[str, pd.DataFrame], asof: pd.Timestamp, cfg: Cfg) -> dict[str, pd.DataFrame]:
    """ข้อมูลดิบเท่าที่ผู้เทรด 'เห็นได้จริง' ณ เวลา asof"""
    out = {}
    for k, df in raw.items():
        if df is None or not len(df):
            out[k] = df
            continue
        out[k] = df[publish_time(k, df.index, cfg) <= asof]
    return out


def _z(x: pd.Series, w: int) -> pd.Series:
    # baseline เลื่อน 1 แถว -> ค่าปัจจุบันไม่ปนใน mean/std ของตัวเอง
    m = x.rolling(w, min_periods=w // 2).mean().shift(1)
    s = x.rolling(w, min_periods=w // 2).std().shift(1)
    return (x - m) / s.replace(0, np.nan)


def build(raw: dict[str, pd.DataFrame], cfg: Cfg) -> pd.DataFrame:
    fk = raw["fut_kl"]
    if fk is None or not len(fk):
        raise ValueError("ไม่มี futures klines")
    idx = pd.date_range(fk.index[0], fk.index[-1], freq="1min", tz="UTC")
    fk = fk.reindex(idx)
    b = pd.DataFrame(index=idx)
    b["close"] = fk["close"].ffill()
    b["open"] = fk["open"].fillna(b["close"].shift(1)).fillna(b["close"])
    b["high"] = fk["high"].fillna(b["close"])
    b["low"] = fk["low"].fillna(b["close"])
    b["qv"] = fk["quote_volume"].fillna(0.0)
    b["fut_delta"] = (2 * fk["taker_buy_quote"] - fk["quote_volume"]).fillna(0.0)

    sk = raw.get("spot_kl")
    if sk is not None and len(sk):
        sk = sk.reindex(idx)
        b["spot_close"] = sk["close"].ffill()
        b["spot_qv"] = sk["quote_volume"].fillna(0.0)
        b["spot_delta"] = (2 * sk["taker_buy_quote"] - sk["quote_volume"]).fillna(0.0)
        # klines ที่ยังไม่เผยแพร่ (หลังจุดสุดท้ายของ spot) ต้องเป็น NaN ไม่ใช่ 0
        last = raw["spot_kl"].index[-1]
        b.loc[b.index > last, ["spot_qv", "spot_delta"]] = np.nan
    else:
        b["spot_close"] = np.nan
        b["spot_qv"] = np.nan
        b["spot_delta"] = np.nan

    decide = idx + ONE_MIN  # เวลาที่ตัดสินใจของแต่ละแถว

    m = raw.get("metrics")
    if m is not None and len(m) and "sum_open_interest_value" in m:
        avail = publish_time("metrics", m.index, cfg)
        mm = pd.DataFrame({"avail": avail, "oi": m["sum_open_interest"].values,
                           "oi_usd": m["sum_open_interest_value"].values}).sort_values("avail")
        j = pd.merge_asof(pd.DataFrame({"decide": decide}), mm, left_on="decide",
                          right_on="avail", direction="backward")
        b["oi"] = j["oi"].values
        b["oi_usd"] = j["oi_usd"].values
    else:
        b["oi"] = np.nan
        b["oi_usd"] = np.nan

    f = raw.get("funding")
    if f is not None and len(f):
        ff = pd.DataFrame({"avail": f.index, "funding": f["funding"].values}).sort_values("avail")
        j = pd.merge_asof(pd.DataFrame({"decide": decide}), ff, left_on="decide",
                          right_on="avail", direction="backward")
        b["funding"] = j["funding"].values
    else:
        b["funding"] = np.nan

    for mk in ("fut", "spot"):
        a = raw.get(f"{mk}_agg1m")
        if a is not None and len(a):
            a = a.reindex(idx)
            last = raw[f"{mk}_agg1m"].index[-1]
            big = (a["big_buy_usd"] - a["big_sell_usd"]).fillna(0.0)
            big[b.index > last] = np.nan
            b[f"{mk}_big_delta"] = big
        else:
            b[f"{mk}_big_delta"] = np.nan

    return add_features(b, cfg)


def add_features(b: pd.DataFrame, cfg: Cfg) -> pd.DataFrame:
    s = cfg.signal
    W = s.baseline
    b = b.copy()
    b["ret_ext"] = b["close"] / b["close"].shift(s.ext_window) - 1
    b["oi_chg"] = b["oi"] / b["oi"].shift(s.oi_window) - 1

    fut_cvd = b["fut_delta"].rolling(s.cvd_window, min_periods=s.cvd_window).sum()
    spot_cvd = b["spot_delta"].rolling(s.cvd_window, min_periods=s.cvd_window).sum()
    b["fut_cvd"] = fut_cvd
    b["spot_cvd"] = spot_cvd
    b["fut_cvd_z"] = _z(fut_cvd, W)
    b["spot_cvd_z"] = _z(spot_cvd, W)

    rv = b["qv"].rolling(s.rv_window, min_periods=s.rv_window).sum()
    base = rv.rolling(W, min_periods=W // 2).median().shift(1)
    b["rvol"] = rv / base.replace(0, np.nan)

    # CVD สะสมแบบต่อเนื่องสำหรับกราฟ (ไม่ใช้ในสัญญาณ)
    b["fut_cvd_cum"] = b["fut_delta"].cumsum()
    b["spot_cvd_cum"] = b["spot_delta"].fillna(0).cumsum()
    return b
