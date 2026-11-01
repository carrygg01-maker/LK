"""
run.py — Pre-Pump Forensic Lab

  python run.py selftest                         # ตรวจเครื่องมือด้วยข้อมูลที่รู้คำตอบ (ไม่ต้องต่อเน็ต)
  python run.py fetch    --symbol ESPORTSUSDT --start 2026-10-18 --end 2026-11-02 [--aggtrades]
  python run.py forensic --symbol ESPORTSUSDT --focus-start 2026-10-25 --focus-end 2026-11-02
  python run.py universe --start 2026-03-01 --end 2026-10-31 --max 120     # Phase 2: ดึงหลายเหรียญ
  python run.py backtest [--symbols A,B,...]                                 # Phase 3: walk-forward
  python run.py oi-latency --symbol ESPORTSUSDT --minutes 60                  # วัดเวลาเผยแพร่ OI จริง

ผลลัพธ์อยู่ใน reports/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import backtest  # noqa: E402
import dashboard  # noqa: E402
import fetch  # noqa: E402
import forensic  # noqa: E402
import synthetic  # noqa: E402
from config import DEFAULT  # noqa: E402

REPORTS = HERE / "reports"


def _oos_line() -> str | None:
    f = REPORTS / "backtest.json"
    if not f.exists():
        return None
    ev = json.loads(f.read_text(encoding="utf-8"))["eval"]
    s = ev.get("summary") or {}
    if not s.get("n"):
        return ev.get("verdict")
    return (f"{ev['verdict']} · {s['n']} ไม้ · expectancy {s['expectancy_pct']:+.3f}% · PF {s['profit_factor']:.2f} · "
            f"precision {ev['precision']*100:.0f}% (ดู reports/backtest.md)")


def _save_forensic(res: dict, sym: str, bt: dict | None = None) -> None:
    REPORTS.mkdir(exist_ok=True)
    if not sym.startswith("SYN"):
        res["answers"]["oos_summary"] = _oos_line()
    md = forensic.to_markdown(res["answers"], DEFAULT)
    (REPORTS / f"{sym}_forensic.md").write_text(md, encoding="utf-8")
    ans = {k: v for k, v in res["answers"].items()}
    (REPORTS / f"{sym}_forensic.json").write_text(json.dumps(ans, indent=2, default=str, ensure_ascii=False),
                                                 encoding="utf-8")
    if len(res["trades"]):
        res["trades"].to_csv(REPORTS / f"{sym}_trades.csv", index=False)
    p = dashboard.write(res, DEFAULT, REPORTS / f"{sym}_replay.html", backtest=bt)
    print(md)
    print(f"\n→ {REPORTS / f'{sym}_forensic.md'}\n→ {p}  (เปิดด้วย browser)")


def cmd_selftest(_a) -> None:
    sys.path.insert(0, str(HERE / "tests"))
    import test_no_lookahead as t  # noqa: E402
    for name in [n for n in dir(t) if n.startswith("test_")]:
        getattr(t, name)()
        print(f"✓ {name}")
    raw, truth = synthetic.make(days=14, seed=1)
    print("\nฝังไว้:", json.dumps(truth, indent=1))
    day = truth["real"][0]["pump_start"][:10]
    res = forensic.run(raw, DEFAULT, focus_start=day, focus_end=day, symbol="SYNTHETIC")
    res["answers"]["lag_sensitivity"] = forensic.lag_sensitivity(raw, DEFAULT, day, day)
    _save_forensic(res, "SYNTHETIC")

    print("\n=== walk-forward บนข้อมูลสังเคราะห์: มี edge vs ไม่มี edge ===")
    for edge in (True, False):
        uni = synthetic.universe(n_symbols=20, months=5, edge=edge)
        prep = backtest.prepare(list(uni), lambda s: uni[s], DEFAULT)
        wf = backtest.walk_forward(prep, DEFAULT)
        ev = backtest.evaluate(prep, wf["oos"], DEFAULT)
        s = ev.get("summary", {})
        print(f"  edge={edge}: verdict={ev['verdict']} n={s.get('n')} "
              f"mean={s.get('mean_net_pct', float('nan')):+.3f}% precision={ev.get('precision', 0):.2f} "
              f"checks={ev.get('checks')}")


def cmd_fetch(a) -> None:
    fetch.fetch_symbol(a.symbol, a.start, a.end, aggtrades=a.aggtrades, big_usd=DEFAULT.data.big_trade_usd)


def cmd_forensic(a) -> None:
    raw = fetch.load_symbol(a.symbol)
    if not len(raw["fut_kl"]):
        sys.exit(f"ยังไม่มีข้อมูล {a.symbol} — รัน: python run.py fetch --symbol {a.symbol} ...")
    res = forensic.run(raw, DEFAULT, a.focus_start, a.focus_end, symbol=a.symbol)
    print("ทดสอบความไวต่อ OI lag 0/5/10/15/30 นาที ...")
    res["answers"]["lag_sensitivity"] = forensic.lag_sensitivity(raw, DEFAULT, a.focus_start, a.focus_end)
    _save_forensic(res, a.symbol)


def cmd_oi_latency(a) -> None:
    """
    วัดจริงว่า OI 5m ของ Binance 'เผยแพร่' ช้ากว่า timestamp กี่นาที และ timestamp หมายถึงต้นหรือปลายช่วง
    - poll openInterestHist (5m) ทุก a.every วินาที บันทึกเวลาที่แถวใหม่ปรากฏครั้งแรก
    - poll openInterest (real-time) คู่กัน เพื่อเทียบว่าค่าในแถว T ตรงกับ real-time ณ เวลาใด
    """
    import time as _t
    import urllib.parse
    seen, rt, rows = {}, [], []
    end = _t.time() + a.minutes * 60
    print(f"วัด {a.symbol} {a.minutes} นาที (ปล่อยรันไว้) ...")
    while _t.time() < end:
        now = pd.Timestamp.now(tz="UTC")
        try:
            q = urllib.parse.urlencode({"symbol": a.symbol, "period": "5m", "limit": 3})
            for r in fetch._http(f"{fetch.FAPI}/futures/data/openInterestHist?{q}") or []:
                ts = pd.Timestamp(r["timestamp"], unit="ms", tz="UTC")
                if ts not in seen:
                    seen[ts] = (now, float(r["sumOpenInterest"]))
            o = fetch._http(f"{fetch.FAPI}/fapi/v1/openInterest?symbol={a.symbol}")
            if o:
                rt.append((pd.Timestamp(o["time"], unit="ms", tz="UTC"), float(o["openInterest"])))
        except Exception as e:  # noqa: BLE001
            print("  error:", e)
        _t.sleep(a.every)
    first_poll = min(v[0] for v in seen.values()) if seen else None
    rts = pd.Series({t: v for t, v in rt}).sort_index()
    for ts, (when, oi) in sorted(seen.items()):
        if first_poll is not None and when <= first_poll + pd.Timedelta(seconds=a.every * 1.5):
            continue  # แถวที่มีอยู่แล้วตอนเริ่มวัด ไม่รู้ว่าปรากฏเมื่อไร
        near = rts.iloc[(abs(rts.index - ts)).argsort()[:1]] if len(rts) else pd.Series(dtype=float)
        near_end = rts.iloc[(abs(rts.index - (ts + pd.Timedelta(minutes=5)))).argsort()[:1]] if len(rts) else pd.Series(dtype=float)
        rows.append({"timestamp": ts, "first_seen": when,
                     "latency_min": round((when - ts).total_seconds() / 60, 2), "hist_oi": oi,
                     "rt_oi_at_ts": float(near.iloc[0]) if len(near) else None,
                     "rt_oi_at_ts+5m": float(near_end.iloc[0]) if len(near_end) else None})
    df = pd.DataFrame(rows)
    REPORTS.mkdir(exist_ok=True)
    df.to_csv(REPORTS / f"oi_latency_{a.symbol}.csv", index=False)
    print(df.to_string())
    if len(df):
        print(f"\nlatency: median {df['latency_min'].median():.2f} นาที · max {df['latency_min'].max():.2f} นาที")
        print("ถ้า hist_oi ใกล้ rt_oi_at_ts มากกว่า rt_oi_at_ts+5m -> timestamp = เวลา snapshot (ไม่ใช่ปลายช่วง)")
        print(f"ตั้ง oi_lag_min ใน config.py ให้ >= max latency (ปัดขึ้น) → ตอนนี้ตั้งไว้ {DEFAULT.data.oi_lag_min}")


def cmd_universe(a) -> None:
    syms = fetch.list_vision_symbols()
    print(f"Vision มี {len(syms)} เหรียญ USDT-M (รวมที่ delist แล้ว)")
    if a.symbols:
        syms = [s for s in a.symbols.split(",") if s]
    # เรียงแบบสุ่มคงที่ — ห้ามเลือกตามผลงาน (เช่น top gainers) เพราะจะเกิด selection bias
    syms = list(pd.Series(syms).sample(frac=1, random_state=42))[: a.max]
    for i, s in enumerate(syms, 1):
        if (fetch.DATA / s / "fut_kl.parquet").exists() and not a.refresh:
            continue
        print(f"[{i}/{len(syms)}] {s}")
        try:
            fetch.fetch_symbol(s, a.start, a.end, aggtrades=False)
        except Exception as e:  # noqa: BLE001
            print(f"  ล้มเหลว: {e}")


def cmd_backtest(a) -> None:
    if a.symbols:
        syms = a.symbols.split(",")
    else:
        syms = sorted(p.name for p in fetch.DATA.iterdir() if (p / "fut_kl.parquet").exists())
    if a.exclude:
        syms = [s for s in syms if s not in a.exclude.split(",")]
    print(f"เตรียม {len(syms)} เหรียญ ...")
    prep = backtest.prepare(syms, fetch.load_symbol, DEFAULT)
    wf = backtest.walk_forward(prep, DEFAULT, min_train_months=a.train_months)
    ev = backtest.evaluate(prep, wf["oos"], DEFAULT)
    fa = backtest.failure_analysis(wf["oos"]) if len(wf["oos"]) else []
    md = backtest.to_markdown(wf, ev, fa, DEFAULT, len(prep))
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "backtest.md").write_text(md, encoding="utf-8")
    if len(wf["oos"]):
        wf["oos"].to_csv(REPORTS / "backtest_oos_trades.csv", index=False)
    (REPORTS / "backtest.json").write_text(json.dumps({"eval": ev, "folds": wf["folds"], "failure": fa},
                                                      default=str, indent=2, ensure_ascii=False), encoding="utf-8")
    print(md)


def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-Pump Forensic Lab")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("selftest")
    f = sp.add_parser("fetch")
    f.add_argument("--symbol", required=True)
    f.add_argument("--start", required=True)
    f.add_argument("--end", required=True)
    f.add_argument("--aggtrades", action="store_true")
    g = sp.add_parser("forensic")
    g.add_argument("--symbol", required=True)
    g.add_argument("--focus-start")
    g.add_argument("--focus-end")
    u = sp.add_parser("universe")
    u.add_argument("--start", required=True)
    u.add_argument("--end", required=True)
    u.add_argument("--max", type=int, default=120)
    u.add_argument("--symbols")
    u.add_argument("--refresh", action="store_true")
    b = sp.add_parser("backtest")
    b.add_argument("--symbols")
    b.add_argument("--exclude", help="เช่น ESPORTSUSDT — กันเหรียญที่ใช้ออกแบบสูตรไม่ให้ปนใน OOS")
    b.add_argument("--train-months", type=int, default=2)
    o = sp.add_parser("oi-latency")
    o.add_argument("--symbol", default="BTCUSDT")
    o.add_argument("--minutes", type=int, default=60)
    o.add_argument("--every", type=int, default=10)
    a = ap.parse_args()
    {"selftest": cmd_selftest, "fetch": cmd_fetch, "forensic": cmd_forensic,
     "universe": cmd_universe, "backtest": cmd_backtest, "oi-latency": cmd_oi_latency}[a.cmd](a)


if __name__ == "__main__":
    main()
