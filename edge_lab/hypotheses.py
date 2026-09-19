"""
hypotheses.py — สมมติฐาน 6 ข้อ แต่ละข้อมีกลไกรองรับ ไม่ได้ขุด pattern มั่ว

กติกาที่ทุกข้อต้องเคารพ:
  * feature ทุกตัวคำนวณจากข้อมูล ณ เวลานั้นหรือก่อนหน้าเท่านั้น
  * rolling stat ใช้ .shift(1) เสมอก่อนเอาไปเทียบ
  * forward return วัดจากราคา "หลัง" เวลาที่ตัดสินใจ
  * คืน pd.Series ของ gross return ต่อไม้ (stats.evaluate จะหักต้นทุนเอง)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BARS = {"8h": 1095.0, "24h": 365.0, "1h": 8760.0, "week": 52.0}


def _fwd_return(close: pd.Series, horizon_bars: int) -> pd.Series:
    return close.shift(-horizon_bars) / close - 1.0


def _causal_z(s: pd.Series, window: int) -> pd.Series:
    """z-score ที่ใช้ได้เฉพาะข้อมูลอดีต — shift(1) กันข้อมูลรั่ว"""
    m = s.rolling(window, min_periods=window // 2).mean().shift(1)
    sd = s.rolling(window, min_periods=window // 2).std().shift(1)
    return (s - m) / sd.replace(0, np.nan)


# ══════════════════════════════════════════════════════════════════ H1
def h1_funding_extreme(klines_1h: dict[str, pd.DataFrame],
                       funding: dict[str, pd.DataFrame],
                       z_thresh: float = 2.0,
                       horizon_h: int = 8) -> pd.Series:
    """
    H1 — FORCED FLOW: funding สุดขั้ว = ฝั่งหนึ่งแออัดและกำลังจ่ายเงินให้อีกฝั่ง
    ตำแหน่งที่ต้องจ่ายต่อเนื่องคือตำแหน่งที่ถูกบีบให้ปิด

    Null: หลัง settlement ทิศทางราคาไม่ขึ้นกับ funding z-score
    Trade: z > +2 -> short (longs แออัด) | z < -2 -> long
    """
    rows = []
    for sym, fdf in funding.items():
        kl = klines_1h.get(sym)
        if kl is None or kl.empty or len(fdf) < 200:
            continue
        z = _causal_z(fdf["rate"], window=90)          # 90 settlements = 30 วัน
        px = kl["close"].reindex(fdf.index, method="ffill")
        fwd = px.shift(-horizon_h) / px - 1.0          # ราคาหลังจากนั้น 8 ชม.
        sig = np.where(z > z_thresh, -1.0, np.where(z < -z_thresh, 1.0, 0.0))
        r = pd.Series(sig, index=fdf.index) * fwd
        rows.append(r[sig != 0].dropna())
    return pd.concat(rows).sort_index() if rows else pd.Series(dtype=float)


# ══════════════════════════════════════════════════════════════════ H2
def h2_cross_sectional_carry(klines_1h: dict[str, pd.DataFrame],
                             funding: dict[str, pd.DataFrame],
                             quantile: float = 0.2) -> pd.Series:
    """
    H2 — CARRY FACTOR แบบ market-neutral
    long เหรียญที่ funding ติดลบสุด (ได้รับเงิน) / short ที่บวกสุด (ต้องจ่าย)
    รีบาลานซ์ทุก 8 ชม. หักต้นทุน turnover จริง

    Null: ผลตอบแทนส่วนเกินของพอร์ตนี้ = 0 หลังหักค่าธรรมเนียม
    """
    fr = pd.DataFrame({s: d["rate"] for s, d in funding.items() if len(d) > 200})
    if fr.shape[1] < 8:
        return pd.Series(dtype=float)
    fr = fr.sort_index()

    px = pd.DataFrame({s: klines_1h[s]["close"] for s in fr.columns
                       if s in klines_1h and not klines_1h[s].empty})
    px = px.reindex(fr.index, method="ffill")
    fwd = px.shift(-8) / px - 1.0

    z = fr.apply(lambda col: _causal_z(col, 90))
    z = z.where(fwd.notna())

    out, prev_w = [], None
    for t in z.index:
        row = z.loc[t].dropna()
        if len(row) < 8:
            continue
        lo_cut, hi_cut = row.quantile(quantile), row.quantile(1 - quantile)
        longs, shorts = row[row <= lo_cut].index, row[row >= hi_cut].index
        if len(longs) == 0 or len(shorts) == 0:
            continue
        w = pd.Series(0.0, index=row.index)
        w[longs] = 0.5 / len(longs)
        w[shorts] = -0.5 / len(shorts)
        ret = float((w * fwd.loc[t].reindex(w.index).fillna(0)).sum())
        # ต้นทุนจาก turnover จริง
        if prev_w is None:
            turn = w.abs().sum()
        else:
            al = w.align(prev_w, fill_value=0.0)
            turn = float((al[0] - al[1]).abs().sum())
        out.append((t, ret - turn * 0.00055))
        prev_w = w
    if not out:
        return pd.Series(dtype=float)
    s = pd.Series(dict(out)).sort_index()
    s.attrs["cost_already_applied"] = True
    return s


# ══════════════════════════════════════════════════════════════════ H3
def _asia_one(kl: pd.DataFrame, move_thresh: float) -> pd.Series:
    """
    H3 — ช่องว่างภูมิศาสตร์ (สนามที่คุณอยู่)
    US session (UTC 13-21) เคลื่อนแรง -> Asia session (UTC 00-08 = 07:00-15:00 ICT)
    ทำอะไรต่อ? สานต่อ หรือย้อนกลับ?
    งานวิจัยตะวันตกแทบไม่แตะช่วงนี้เพราะเขานอนอยู่

    Null: return ช่วง Asia ไม่ขึ้นกับ return ของ US session คืนก่อนหน้า
    """
    if kl is None or kl.empty:
        return pd.Series(dtype=float)
    c = kl["close"]
    h = c.index.hour

    us_open = c[h == 13].rename("us_o")
    us_close = c[h == 20].rename("us_c")
    asia_open = c[h == 0].rename("as_o")
    asia_close = c[h == 7].rename("as_c")

    df = pd.DataFrame({
        "us_o": us_open.groupby(us_open.index.date).last(),
        "us_c": us_close.groupby(us_close.index.date).last(),
        "as_o": asia_open.groupby(asia_open.index.date).last(),
        "as_c": asia_close.groupby(asia_close.index.date).last(),
    })
    df.index = pd.to_datetime(df.index, utc=True)
    df["us_ret"] = df["us_c"] / df["us_o"] - 1
    # Asia ของ "วันถัดไป" ตอบสนองต่อ US ของเมื่อคืน
    df["asia_ret_next"] = (df["as_c"] / df["as_o"] - 1).shift(-1)
    df = df.dropna()

    big = df[df["us_ret"].abs() > move_thresh]
    # สมมติฐาน: Asia ย้อนกลับทิศของ US move (รีเทลเอเชียขายรับ/ซื้อรับ)
    return (-np.sign(big["us_ret"]) * big["asia_ret_next"]).sort_index()


def h3_asia_session(klines_1h: dict[str, pd.DataFrame],
                    move_thresh: float = 0.015,
                    max_coins: int = 15) -> pd.Series:
    """
    รวมหลายเหรียญ ไม่ใช่ BTC ตัวเดียว
    เหตุผล: BTC ให้ n แค่ ~200 ไม้ใน 5 ปี ซึ่งน้อยเกินกว่าจะสรุปอะไรได้
    (ผล synthetic ฟ้องชัด: n=178 -> ไม่มีทางผ่านเกณฑ์แม้ edge มีจริง)
    """
    rows = []
    for sym in list(klines_1h)[:max_coins]:
        r = _asia_one(klines_1h[sym], move_thresh)
        if len(r):
            rows.append(r)
    return pd.concat(rows).sort_index() if rows else pd.Series(dtype=float)


# ══════════════════════════════════════════════════════════════════ H4
def h4_new_listing(klines_1h: dict[str, pd.DataFrame],
                   onboard: pd.Series,
                   btc: pd.DataFrame,
                   window_days: int = 14) -> pd.Series:
    """
    H4 — MM ต้องสร้าง inventory ตอน perp list ใหม่ = flow ที่ถูกบังคับ
    วัดผลตอบแทนส่วนเกินเทียบ BTC ในช่วง N วันแรกหลัง list

    Null: excess return ช่วงแรกหลัง list = 0
    ตั้งใจให้เป็น short bias (เหรียญใหม่มักถูก dump หลัง hype แรก)
    """
    if btc is None or btc.empty:
        return pd.Series(dtype=float)
    b = btc["close"]
    out = []
    for sym, kl in klines_1h.items():
        if kl.empty or sym not in onboard.index:
            continue
        t0 = onboard[sym]
        seg = kl[(kl.index >= t0) & (kl.index < t0 + pd.Timedelta(days=window_days))]
        if len(seg) < window_days * 20:
            continue
        bseg = b.reindex(seg.index, method="ffill")
        if bseg.isna().all():
            continue
        coin_r = seg["close"].iloc[-1] / seg["close"].iloc[0] - 1
        btc_r = bseg.iloc[-1] / bseg.iloc[0] - 1
        out.append((t0, -(coin_r - btc_r)))   # short เหรียญ / long BTC
    if not out:
        return pd.Series(dtype=float)
    return pd.Series(dict(out)).sort_index()


# ══════════════════════════════════════════════════════════════════ H5
def _weekend_one(kl: pd.DataFrame, move_thresh: float) -> pd.Series:
    """
    H5 — สุดสัปดาห์ liquidity หาย สถาบันไม่อยู่ market maker ลด inventory
    ของถูกบังคับให้เคลื่อนแรงเกินจริงเพราะไม่มีใครรับ -> จันทร์ควรย้อนกลับ

    Null: return วันจันทร์ไม่ขึ้นกับ return สุดสัปดาห์
    Entry: เสาร์ 00:00 UTC ถึง จันทร์ 00:00 UTC = weekend move
           ถือ จันทร์ 00:00 -> อังคาร 00:00 ทิศตรงข้าม
    """
    if kl is None or kl.empty:
        return pd.Series(dtype=float)
    d = kl["close"].resample("1D").last().dropna()
    dow = d.index.dayofweek                       # 0=Mon .. 5=Sat, 6=Sun
    sat = d[dow == 5]
    out = []
    for ts in sat.index:
        mon, tue = ts + pd.Timedelta(days=2), ts + pd.Timedelta(days=3)
        if mon not in d.index or tue not in d.index:
            continue
        wk = d[mon] / d[ts] - 1
        if abs(wk) < move_thresh:
            continue
        mon_ret = d[tue] / d[mon] - 1
        out.append((mon, -np.sign(wk) * mon_ret))
    if not out:
        return pd.Series(dtype=float)
    return pd.Series(dict(out)).sort_index()


def h5_weekend_overshoot(klines_1h: dict[str, pd.DataFrame],
                         move_thresh: float = 0.02,
                         max_coins: int = 15) -> pd.Series:
    """
    รวมหลายเหรียญ — สุดสัปดาห์มีแค่ 52 ครั้ง/ปี ใช้เหรียญเดียวไม่มีวันพอ
    (synthetic ให้ OOS n=28 ซึ่งบอกอะไรไม่ได้เลย)

    ข้อควรระวัง: เหรียญต่างๆ เคลื่อนไปทางเดียวกันในสุดสัปดาห์เดียวกัน
    n ที่เพิ่มขึ้นจึงไม่ได้แปลว่าข้อมูลอิสระเพิ่มขึ้นตามสัดส่วน
    block bootstrap ช่วยชดเชยตรงนี้บางส่วน แต่ให้ตีความ t-stat อย่างระมัดระวัง
    """
    rows = []
    for sym in list(klines_1h)[:max_coins]:
        r = _weekend_one(klines_1h[sym], move_thresh)
        if len(r):
            rows.append(r)
    return pd.concat(rows).sort_index() if rows else pd.Series(dtype=float)


# ══════════════════════════════════════════════════════════════════ H6
def h6_basis_extreme(perp_1h: dict[str, pd.DataFrame],
                     spot_1h: dict[str, pd.DataFrame],
                     z_thresh: float = 2.5,
                     horizon_h: int = 24) -> pd.Series:
    """
    H6 — perp premium เหนือ spot = leverage ฝั่ง long ตึง
    สเปรดนี้กลับเข้าหาศูนย์เสมอเพราะมี arbitrageur คอยบีบ
    คำถามคือ "ราคา" เป็นฝ่ายกลับ หรือ funding เป็นฝ่ายกลับ

    Null: basis z-score ไม่ทำนาย forward return ของ perp
    """
    rows = []
    for sym, p in perp_1h.items():
        s = spot_1h.get(sym)
        if s is None or s.empty or p.empty:
            continue
        idx = p.index.intersection(s.index)
        if len(idx) < 2000:
            continue
        basis = (p["close"].reindex(idx) / s["close"].reindex(idx) - 1.0)
        z = _causal_z(basis, window=720)           # 30 วัน
        px = p["close"].reindex(idx)
        fwd = px.shift(-horizon_h) / px - 1.0
        sig = np.where(z > z_thresh, -1.0, np.where(z < -z_thresh, 1.0, 0.0))
        r = pd.Series(sig, index=idx) * fwd
        # เข้าไม้ซ้อนกันทำให้นับเกิน -> เว้นช่วงเท่ากับ horizon
        r = r[sig != 0].dropna()
        if len(r):
            r = r.iloc[::horizon_h]
        rows.append(r)
    return pd.concat(rows).sort_index() if rows else pd.Series(dtype=float)


REGISTRY = {
    "H1_funding_extreme":   dict(fn=h1_funding_extreme,      horizon="8h",
                                 desc="funding สุดขั้ว -> fade ฝั่งที่แออัด"),
    "H2_xs_carry":          dict(fn=h2_cross_sectional_carry, horizon="8h",
                                 desc="carry factor market-neutral"),
    "H3_asia_session":      dict(fn=h3_asia_session,          horizon="24h",
                                 desc="Asia fade US session move"),
    "H4_new_listing":       dict(fn=h4_new_listing,           horizon="week",
                                 desc="short เหรียญใหม่ 14 วันแรก vs BTC"),
    "H5_weekend":           dict(fn=h5_weekend_overshoot,     horizon="week",
                                 desc="weekend overshoot -> จันทร์ย้อน"),
    "H6_basis":             dict(fn=h6_basis_extreme,         horizon="24h",
                                 desc="perp-spot basis สุดขั้ว -> reversion"),
}
