#!/usr/bin/env python3
"""
run.py — โรงงานวิจัย: โยนสมมติฐานเข้าไป ได้คำตอบออกมาภายในวันเดียว

    python run.py --coins 40 --start 2021-01-01        # รันจริง (ต้องต่อ Binance ได้)
    python run.py --synthetic                          # ทดสอบว่า pipeline ทำงานถูก
    python run.py --only H1_funding_extreme H6_basis   # รันเฉพาะบางข้อ

ผลลัพธ์: report.md + results.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import stats as S            # noqa: E402
import hypotheses as H       # noqa: E402

HERE = Path(__file__).parent


# ───────────────────────────────────────────────── synthetic (สำหรับตรวจโค้ด)
def build_synthetic(n_coins=20, days=900, seed=11, edge_bps=0.0):
    """
    ข้อมูลปลอมสำหรับตรวจโค้ด

    edge_bps = 0   -> random walk ล้วน ไม่มี edge  => pipeline ต้องไม่เจออะไร
    edge_bps > 0   -> ฝัง edge จริงไว้ใน H1        => pipeline ต้องเจอให้ได้

    การทดสอบสองทางนี้สำคัญพอกัน: เครื่องมือที่ไม่เจออะไรเลย
    กับเครื่องมือที่เจอทุกอย่าง ใช้ไม่ได้ทั้งคู่
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=days * 24, freq="1h", tz="UTC")
    perp, spot, fund = {}, {}, {}
    onboard = {}
    for i in range(n_coins):
        sym = "BTCUSDT" if i == 0 else f"C{i:02d}USDT"
        r = rng.normal(0, 0.012, len(idx))

        if edge_bps > 0:
            # funding สุ่มก่อน แล้วฝัง drift ย้อนทิศในราคาช่วง 8 ชม.ถัดไป
            fidx_e = idx[::8]
            rates = rng.normal(0.0001, 0.0004, len(fidx_e))
            rs = pd.Series(rates, index=fidx_e)
            z = ((rs - rs.rolling(90, min_periods=45).mean().shift(1))
                 / rs.rolling(90, min_periods=45).std().shift(1)).fillna(0.0)
            drift = np.zeros(len(idx))
            for k, t in enumerate(fidx_e):
                zz = z.iloc[k]
                if abs(zz) > 2.0:
                    lo = k * 8
                    drift[lo:lo + 8] += -np.sign(zz) * (edge_bps / 1e4) / 8
            r = r + drift
            fund[sym] = pd.DataFrame({"rate": rates}, index=fidx_e)

        px = 100 * np.exp(np.cumsum(r))
        df = pd.DataFrame({"open": px, "high": px * 1.002, "low": px * 0.998,
                           "close": px, "volume": rng.lognormal(10, 1, len(idx)),
                           "quote_volume": rng.lognormal(12, 1, len(idx)),
                           "trades": 100, "taker_buy_base": 1.0,
                           "taker_buy_quote": 1.0}, index=idx)
        perp[sym] = df
        spot[sym] = df.assign(close=df["close"] * (1 + rng.normal(0, 0.0004, len(idx))))
        if sym not in fund:
            fidx = idx[::8]
            fund[sym] = pd.DataFrame(
                {"rate": rng.normal(0.0001, 0.0004, len(fidx))}, index=fidx)
        onboard[sym] = idx[0] + pd.Timedelta(days=int(rng.integers(0, 300)))
    btc = perp["BTCUSDT"]
    return perp, spot, fund, pd.Series(onboard), btc


# ───────────────────────────────────────────────────────────── real data load
def load_real(n_coins: int, start: str):
    import data as D
    print(f"→ ดึงรายชื่อ perpetual ...")
    uni = D.perp_symbols()
    uni = uni[~uni["symbol"].str.contains("USDC|1000000", regex=True)]
    syms = uni["symbol"].head(n_coins).tolist()
    if "BTCUSDT" not in syms:
        syms.insert(0, "BTCUSDT")
    onboard = uni.set_index("symbol")["onboard"]

    perp, spot, fund = {}, {}, {}
    for i, s in enumerate(syms, 1):
        print(f"  [{i}/{len(syms)}] {s}")
        try:
            perp[s] = D.klines(s, "1h", start, market="futures")
            fund[s] = D.funding(s, start)
            try:
                spot[s] = D.klines(s, "1h", start, market="spot")
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            print(f"      ข้าม ({e})")
    btc = perp.get("BTCUSDT", pd.DataFrame())
    return perp, spot, fund, onboard, btc


# ────────────────────────────────────────────────────────────────── evaluate
def run_one(name: str, series: pd.Series, horizon: str) -> tuple[S.Result, S.Result]:
    bpy = H.BARS[horizon]
    cost = 0.0 if series.attrs.get("cost_already_applied") else S.COST_ROUND_TRIP
    if series.empty:
        empty = S.Result(name=name, note="ไม่มีข้อมูลพอ")
        return empty, empty
    s = series.sort_index()
    cut = int(len(s) * 0.70)
    is_, oos = s.iloc[:cut], s.iloc[cut:]
    r_is = S.evaluate(is_, f"{name} [IS]", bpy, cost=cost)
    r_oos = S.evaluate(oos, f"{name} [OOS]", bpy, cost=cost,
                       note="ตัวเลขนี้เท่านั้นที่นับ")
    return r_is, r_oos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", type=int, default=40)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--edge-bps", type=float, default=0.0,
                    help="ฝัง edge ขนาดนี้ (bps) ลงใน synthetic เพื่อเช็คว่าเครื่องมือจับได้")
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()

    if a.synthetic:
        print("โหมด SYNTHETIC — ข้อมูลสุ่มล้วน ไม่มี edge ซ่อนอยู่")
        perp, spot, fund, onboard, btc = build_synthetic(edge_bps=a.edge_bps)
    else:
        perp, spot, fund, onboard, btc = load_real(a.coins, a.start)

    names = a.only or list(H.REGISTRY)
    results = []
    for name in names:
        spec = H.REGISTRY[name]
        print(f"\n▶ {name} — {spec['desc']}")
        fn = spec["fn"]
        try:
            if name == "H1_funding_extreme":
                s = fn(perp, fund)
            elif name == "H2_xs_carry":
                s = fn(perp, fund)
            elif name == "H3_asia_session":
                s = fn(perp)
            elif name == "H4_new_listing":
                s = fn(perp, onboard, btc)
            elif name == "H5_weekend":
                s = fn(perp)
            elif name == "H6_basis":
                s = fn(perp, spot)
            else:
                continue
        except Exception as e:  # noqa: BLE001
            print(f"   ERROR: {e}")
            continue
        r_is, r_oos = run_one(name, s, spec["horizon"])
        print(f"   IS : n={r_is.n:5d}  net={r_is.mean_bps:7.2f}bps  t={r_is.tstat:6.2f}")
        print(f"   OOS: n={r_oos.n:5d}  net={r_oos.mean_bps:7.2f}bps  t={r_oos.tstat:6.2f}  "
              f"{'✓ ผ่าน' if r_oos.passed else '✗ ไม่ผ่าน'}")
        results += [r_is, r_oos]

    df = S.table(results)
    thr = S.deflated_threshold(len(names))
    out_csv = HERE / "results.csv"
    df.to_csv(out_csv, index=False)

    lines = [
        "# ผลทดสอบสมมติฐาน",
        "",
        f"- โหมด: {'SYNTHETIC (ควรไม่ผ่านทุกข้อ)' if a.synthetic else 'ข้อมูลจริง'}",
        f"- ต้นทุนไป-กลับที่หัก: {S.COST_ROUND_TRIP*1e4:.1f} bps",
        f"- เกณฑ์ t หลังปรับ multiple testing ({len(names)} ข้อ): |t| > {thr:.2f}",
        f"- เกณฑ์ผ่าน: n≥{S.MIN_EVENTS} และ CI ล่าง > 0 และ |t| > 3.0 และ Sharpe > 0.5",
        "",
        df.to_markdown(index=False),
        "",
        "## อ่านยังไง",
        "",
        "`mean_bps` คือกำไรเฉลี่ยต่อไม้ **หลังหักค่าธรรมเนียมแล้ว** ถ้าติดลบคือกลยุทธ์นี้จ่ายค่าธรรมเนียมมากกว่าที่หาได้",
        "",
        "`ci_low_bps` สำคัญกว่า `mean_bps` — ถ้าขอบล่างของช่วงความเชื่อมั่นยังต่ำกว่าศูนย์ แปลว่าเรายังแยกไม่ออกว่าเป็น edge หรือโชค",
        "",
        "แถว `[IS]` ใช้ดูว่าโค้ดทำงานถูกเท่านั้น **ห้ามใช้ตัดสินใจ** แถว `[OOS]` เท่านั้นที่นับ",
    ]
    (HERE / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nเขียนผลลง {HERE/'report.md'} และ {out_csv}")

    if a.synthetic and a.edge_bps > 0:
        h1 = [r for r in results if r.name.startswith("H1") and "[OOS]" in r.name]
        print("\n=== ตรวจความไว ===")
        ok = bool(h1 and h1[0].passed)
        print(f"ฝัง edge {a.edge_bps:.0f} bps ไว้ใน H1 -> "
              f"{'เครื่องมือจับได้ ✓' if ok else 'เครื่องมือจับไม่ได้ ✗'}")
        if h1:
            print(f"  H1 OOS: n={h1[0].n} net={h1[0].mean_bps:.2f}bps t={h1[0].tstat:.2f}")
    elif a.synthetic:
        bad = [r for r in results if r.passed and "[OOS]" in r.name]
        print("\n=== ตรวจโค้ด ===")
        print(f"สมมติฐานที่ 'ผ่าน' บนข้อมูลสุ่ม: {len(bad)}  (ต้องเป็น 0)")
        for r in bad:
            print(f"  !! {r.name} n={r.n} t={r.tstat:.2f} — น่าจะมี lookahead leak")
        print("ผลลัพธ์:", "โค้ดสะอาด" if not bad else "มีบั๊ก ต้องแก้")


if __name__ == "__main__":
    main()
