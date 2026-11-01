"""
run.py — Pre-Pump Forensic Lab

  python run.py selftest                         # ตรวจเครื่องมือด้วยข้อมูลที่รู้คำตอบ (ไม่ต้องต่อเน็ต)
  python run.py fetch    --symbol ESPORTSUSDT --start 2026-10-18 --end 2026-11-02 [--aggtrades]
  python run.py forensic --symbol ESPORTSUSDT --focus-start 2026-10-25 --focus-end 2026-11-02
  python run.py universe --start 2026-03-01 --end 2026-10-31 --max 120     # Phase 2: ดึงหลายเหรียญ
  python run.py lock --name exp1 --train-start 2026-03-01 --val-start 2026-07-01 --test-start 2026-09-01 --test-end 2026-10-31
  python run.py backtest --manifest experiments/exp1.json                    # DEV: walk-forward บน validation
  python run.py backtest --manifest experiments/exp1.json --open-test        # เปิดชุด test (นับครั้งแรกเท่านั้น)
  python run.py oi-latency --symbol ESPORTSUSDT --hours 48                     # วัดเวลาเผยแพร่ OI จริง (24–72 ชม.)

ผลลัพธ์อยู่ใน reports/
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import backtest  # noqa: E402
import dashboard  # noqa: E402
import fetch  # noqa: E402
import forensic  # noqa: E402
import experiment  # noqa: E402
import quality  # noqa: E402
import synthetic  # noqa: E402
from config import DEFAULT  # noqa: E402

import hashlib  # noqa: E402

CFG_HASH = hashlib.sha256(json.dumps(DEFAULT.to_dict(), sort_keys=True).encode()).hexdigest()[:12]

REPORTS = HERE / "reports"


def _oos_line() -> str | None:
    f = REPORTS / "backtest_test.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    ev = d["evals"]["main"]
    ev["verdict"] = d["verdict"]["verdict"]
    s = ev.get("summary") or {}
    if not s.get("n"):
        return ev.get("verdict")
    return (f"{ev['verdict']} · {s['n']} ไม้ · expectancy {s['expectancy_pct']:+.3f}% · PF {s['profit_factor']:.2f} · "
            f"precision {ev['precision']*100:.0f}% (ดู reports/backtest_test.md)")


def _save_forensic(res: dict, sym: str, bt: dict | None = None) -> None:
    REPORTS.mkdir(exist_ok=True)
    if not sym.startswith("SYN"):
        res["answers"]["oos_summary"] = _oos_line()
    res["answers"]["config_hash"] = CFG_HASH
    res["answers"]["run_at"] = str(pd.Timestamp.now(tz="UTC"))
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
    res["answers"]["verdict"] = forensic.verdict(res["answers"], DEFAULT)
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
    q = quality.symbol_quality(a.symbol, fetch.load_symbol(a.symbol), a.start, a.end)
    p = quality.write([q], REPORTS / f"data_quality_{a.symbol}.json")
    print(f"\nคุณภาพข้อมูล: {q['status']} {q['reasons'] or ''} {q['warnings'] or ''}\n→ {p}")


def cmd_forensic(a) -> None:
    raw = fetch.load_symbol(a.symbol)
    if not len(raw["fut_kl"]):
        sys.exit(f"ยังไม่มีข้อมูล {a.symbol} — รัน: python run.py fetch --symbol {a.symbol} ...")
    q = quality.symbol_quality(a.symbol, raw)
    quality.write([q], REPORTS / "data_quality_report.json")
    print(f"คุณภาพข้อมูล: {q['status']} {q['reasons'] or ''} {q['warnings'] or ''}")
    res = forensic.run(raw, DEFAULT, a.focus_start, a.focus_end, symbol=a.symbol)
    res["answers"]["data_quality"] = {"status": q["status"], "reasons": q["reasons"], "warnings": q["warnings"]}
    me = res["answers"].get("main_episode")
    if me:
        br = pd.Timestamp(me["breakout_time"])
        res["answers"]["feature_coverage_pre_breakout"] = quality.window_coverage(
            res["bars"], br - pd.Timedelta(minutes=DEFAULT.label.lead_window), br)
    print("ทดสอบความไวต่อ OI lag ...")
    res["answers"]["lag_sensitivity"] = forensic.lag_sensitivity(raw, DEFAULT, a.focus_start, a.focus_end)
    res["answers"]["verdict"] = forensic.verdict(res["answers"], DEFAULT)
    _save_forensic(res, a.symbol)


def cmd_oi_latency(a) -> None:
    """
    วัดเวลาเผยแพร่ OI 5m จริง — ควรรัน 24–72 ชม. (ปล่อยไว้ใน terminal/tmux; Ctrl+C แล้วรันต่อได้)

    บันทึกลง reports/oi_latency_<SYM>.csv แบบต่อท้ายทันทีที่เจอแถวใหม่ (ไม่หายถ้าเครื่องดับ)
      timestamp       = เวลาในแถว openInterestHist
      first_seen      = เวลาเครื่องเราเห็นแถวนั้นครั้งแรก (= เวลาที่ระบบอ่านได้จริง)
      latency_min     = first_seen - timestamp  (ความละเอียด ≈ --every วินาที)
      rt_oi_*         = OI real-time ที่เก็บเองใกล้ timestamp และ timestamp+5m -> บอกว่าค่าคือ snapshot ตอนไหน
    """
    import csv
    import math
    import time as _t
    import urllib.parse
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"oi_latency_{a.symbol}.csv"
    rt_file = REPORTS / f"oi_realtime_{a.symbol}.csv"
    seen = set()
    if out.exists():
        seen = set(pd.read_csv(out)["timestamp"].astype(str))
    started = pd.Timestamp.now(tz="UTC")
    end = _t.time() + a.hours * 3600
    print(f"วัด {a.symbol} {a.hours} ชม. ทุก {a.every}s → {out}")
    first_round = True
    offset_s, rtt_s = 0.0, None
    while _t.time() < end:
        try:
            # นาฬิกาเครื่องเทียบ server Binance: offset = server - กึ่งกลางช่วงที่ส่งคำขอ
            t_a = _t.time()
            st = fetch._http(f"{fetch.FAPI}/fapi/v1/time")
            t_b = _t.time()
            if st:
                rtt_s = t_b - t_a
                offset_s = st["serverTime"] / 1000 - (t_a + t_b) / 2
        except Exception as e:  # noqa: BLE001
            print("  clock check error:", e)
        now = pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=offset_s)   # เวลาแบบแก้ clock skew แล้ว
        try:
            o = fetch._http(f"{fetch.FAPI}/fapi/v1/openInterest?symbol={a.symbol}")
            if o:
                new = not rt_file.exists()
                with rt_file.open("a", newline="") as fh:
                    w = csv.writer(fh)
                    if new:
                        w.writerow(["polled_at", "time", "open_interest"])
                    w.writerow([now.isoformat(), pd.Timestamp(o["time"], unit="ms", tz="UTC").isoformat(), o["openInterest"]])
            q = urllib.parse.urlencode({"symbol": a.symbol, "period": "5m", "limit": 3})
            for r in fetch._http(f"{fetch.FAPI}/futures/data/openInterestHist?{q}") or []:
                ts = pd.Timestamp(r["timestamp"], unit="ms", tz="UTC")
                key = str(ts)
                if key in seen:
                    continue
                seen.add(key)
                if first_round:
                    continue  # มีอยู่ก่อนเริ่มวัด ไม่รู้ว่าปรากฏเมื่อไร
                new = not out.exists()
                with out.open("a", newline="") as fh:
                    w = csv.writer(fh)
                    if new:
                        w.writerow(["timestamp", "first_seen", "latency_min", "hist_oi", "clock_offset_s", "rtt_s"])
                    w.writerow([key, now.isoformat(), round((now - ts).total_seconds() / 60, 3), r["sumOpenInterest"],
                                round(offset_s, 3), round(rtt_s, 3) if rtt_s is not None else None])
                print(f"  {key}  เห็นหลัง {(now - ts).total_seconds() / 60:.2f} นาที")
            first_round = False
        except Exception as e:  # noqa: BLE001
            print("  error:", e)
        _t.sleep(a.every)
    oi_latency_summary(a.symbol, a.every)


def oi_latency_summary(sym: str, every: int = 15) -> None:
    out = REPORTS / f"oi_latency_{sym}.csv"
    if not out.exists():
        print("ยังไม่มีข้อมูล")
        return
    df = pd.read_csv(out)
    lat = df["latency_min"]
    hrs = (pd.to_datetime(df["first_seen"]).max() - pd.to_datetime(df["first_seen"]).min()).total_seconds() / 3600
    p50, p95, p99, mx = lat.quantile([.5, .95, .99]).tolist() + [lat.max()]
    rec = int(math.ceil(max(p99, 0) + every / 60 + 1))   # p99 + ความละเอียดการ poll + เผื่อ 1 นาที
    if rec > DEFAULT.data.oi_lag_conservative_min:
        print(f"⚠️  latency จริงสูงกว่า lag อนุรักษนิยม ({DEFAULT.data.oi_lag_conservative_min}) — ต้องเพิ่ม oi_lag_conservative_min "
              "ก่อนล็อก manifest")
    print(f"\n{len(df)} แถว ใน {hrs:.1f} ชม. · latency p50 {p50:.2f} · p95 {p95:.2f} · p99 {p99:.2f} · max {mx:.2f} นาที")
    if hrs < 24:
        print("⚠️  วัดไม่ถึง 24 ชม. — ยังไม่ควรใช้ค่านี้ตั้ง oi_lag_min")
    if "clock_offset_s" in df:
        off = df["clock_offset_s"].abs()
        print(f"clock offset (เครื่อง vs Binance) median {df['clock_offset_s'].median():+.3f}s · max |{off.max():.3f}|s · "
              f"rtt median {df['rtt_s'].median():.3f}s — latency ในไฟล์แก้ offset แล้ว")
        if off.max() > 1:
            print("⚠️  นาฬิกาเครื่องคลาด > 1 วินาที — เปิด NTP sync แล้ววัดใหม่จะดีกว่า")
    print("หมายเหตุ: latency ที่วัดวันนี้ใช้กับข้อมูลอดีตได้แค่เป็นสมมติฐาน — ผลต้องผ่านที่ oi_lag_conservative_min "
          f"({DEFAULT.data.oi_lag_conservative_min} นาที) ด้วย")
    print(f"แนะนำ oi_lag_min = {rec} (ตอนนี้ {DEFAULT.data.oi_lag_min}) · ถ้า max ≫ p99 ให้ดูช่วงเวลาที่ช้าผิดปกติใน CSV")
    rt = REPORTS / f"oi_realtime_{sym}.csv"
    if rt.exists():
        r = pd.read_csv(rt)
        r["time"] = pd.to_datetime(r["time"], utc=True)
        r = r.drop_duplicates("time").set_index("time")["open_interest"].sort_index()
        d0, d5 = [], []
        for _, row in df.iterrows():
            ts = pd.Timestamp(row["timestamp"])
            for tgt, acc in ((ts, d0), (ts + pd.Timedelta(minutes=5), d5)):
                k = r.index.get_indexer([tgt], method="nearest")[0]
                if k >= 0 and abs(r.index[k] - tgt) <= pd.Timedelta(seconds=every * 2):
                    acc.append(abs(r.iloc[k] - row["hist_oi"]) / row["hist_oi"])
        if d0 and d5:
            print(f"ค่าในแถว T ห่างจาก OI real-time ณ T: {np.median(d0):.4%} · ณ T+5m: {np.median(d5):.4%} "
                  f"→ timestamp น่าจะหมายถึง {'เวลา snapshot (ต้นช่วง)' if np.median(d0) < np.median(d5) else 'ปลายช่วง 5 นาที'}")


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


def _symbols_with_data() -> list[str]:
    return sorted(p.name for p in fetch.DATA.iterdir() if (p / "fut_kl.parquet").exists()) if fetch.DATA.exists() else []


def cmd_lock(a) -> None:
    """ล็อกการทดลองก่อนดูผล: universe / design symbols / splits / code / config / data hash"""
    uni = a.symbols.split(",") if a.symbols else _symbols_with_data()
    design = [s for s in (a.design or "").split(",") if s]
    p = experiment.create(a.name, uni, design, a.train_start, a.val_start, a.test_start, a.test_end,
                          DEFAULT.to_dict(), fetch.DATA, note=a.note or "")
    m = experiment.load(p)
    print(f"ล็อกแล้ว → {p}\nuniverse {len(m['universe'])} เหรียญ · design (ไม่อยู่ใน test) {m['design_symbols']}\n"
          f"splits {m['splits']}\ncode {m['code_hash']} · git {m['git']}")
    if m["git"].get("dirty"):
        print("⚠️  มีไฟล์ที่ยังไม่ commit — commit แล้ว commit manifest นี้ด้วย เพื่อให้มีหลักฐานว่าล็อกก่อนเห็นผล")


def cmd_backtest(a) -> None:
    mpath = Path(a.manifest)
    m = experiment.load(mpath)
    syms = m["universe"]
    sp = m["splits"]
    drift = experiment.drift(m, DEFAULT.to_dict(), fetch.DATA)
    if drift:
        print("⚠️  DRIFT:", *drift, sep="\n   ")
    qrep = [quality.symbol_quality(s, fetch.load_symbol(s)) for s in syms]
    quality.write(qrep, REPORTS / "data_quality_report.json")
    ready = [q["symbol"] for q in qrep if q["status"] != "not_ready"]
    ready_share = len(ready) / len(syms) if syms else 0.0
    print(f"universe {len(syms)} · ข้อมูลใช้ได้ {len(ready)} ({ready_share:.0%}) — เหรียญที่ไม่พร้อมไม่ถูกเปลี่ยนตัวแทน")
    REPORTS.mkdir(exist_ok=True)

    if not a.open_test:
        # DEV: walk-forward บน train+validation เท่านั้น ข้อมูลดิบถูกตัดก่อน test_start ตามเวลาเผยแพร่
        prep = backtest.prepare(ready, fetch.load_symbol, DEFAULT, cut_before=sp["test"][0])
        n_train = len(pd.period_range(sp["train"][0], sp["validation"][0], freq="M")) - 1
        wf = backtest.walk_forward(prep, DEFAULT, min_train_months=max(1, n_train))
        ev = backtest.evaluate(prep, wf["oos"], DEFAULT)
        fa = backtest.failure_analysis(wf["oos"]) if len(wf["oos"]) else []
        md = "> DEV MODE — walk-forward บน validation เท่านั้น ชุด test ยังปิดอยู่ ผลนี้ใช้ปรับปรุงได้ แต่ไม่ใช่คำตัดสิน\n\n"
        md += backtest.to_markdown(wf, ev, fa, DEFAULT, len(prep))
        (REPORTS / "backtest_dev.md").write_text(md, encoding="utf-8")
        print(md)
        return

    prior = len(m["test_runs"])
    if prior:
        print(f"⚠️  ชุด test ถูกเปิดไปแล้ว {prior} ครั้ง — ผลครั้งนี้จะเป็น INCONCLUSIVE (ไม่ใช่การทดสอบแบบปิดตา)")
    from dataclasses import replace
    evs = {}
    for tag, lag in (("main", DEFAULT.data.oi_lag_min), ("conservative", DEFAULT.data.oi_lag_conservative_min)):
        cfg = replace(DEFAULT, data=replace(DEFAULT.data, oi_lag_min=lag))
        print(f"\n=== TEST · OI lag {lag} นาที ===")
        prep = backtest.prepare(ready, fetch.load_symbol, cfg)
        ft = backtest.final_test(prep, cfg, sp["train"][0], sp["test"][0], sp["test"][1])
        ev = backtest.evaluate(prep, ft["oos"], cfg) if len(ft["oos"]) else {"verdict": "ไม่มีเทรด", "summary": {"n": 0}, "checks": {}}
        ev.update({"chosen": ft["chosen"], "unknown_share": ft.get("unknown_share"), "oi_lag_min": lag})
        evs[tag] = ev
        if len(ft["oos"]):
            ft["oos"].to_csv(REPORTS / f"backtest_test_trades_lag{lag}.csv", index=False)
        del prep
    v = backtest.three_way(evs["main"], evs["conservative"],
                           {"drift": drift, "prior_test_runs": prior, "ready_share": ready_share})
    n_run = experiment.record_test_run(mpath, {"verdict": v["verdict"], "why": v["why"],
                                               "main": {k: evs["main"].get(k) for k in ("summary", "chosen", "unknown_share", "checks")},
                                               "conservative": {k: evs["conservative"].get(k) for k in ("summary", "chosen", "unknown_share", "checks")}})
    L = [f"# Final Test — {m['name']}\n", f"## Verdict: **{v['verdict']}**\n"] + [f"- {w}" for w in v["why"]]
    L.append(f"\nเปิดชุด test ครั้งที่ {n_run} · splits {sp} · universe {len(syms)} (ใช้ได้ {len(ready)})\n")
    L.append("| | lag ปกติ | lag อนุรักษนิยม |\n|---|---|---|")
    def g(ev, k, f="{:+.3f}"):
        x = (ev.get("summary") or {}).get(k)
        return "-" if x is None else f.format(x)
    for lab, k, f in [("OI lag (นาที)", None, None), ("เทรด", "n", "{}"), ("Expectancy %", "expectancy_pct", "{:+.3f}"),
                      ("Profit Factor", "profit_factor", "{:.2f}"), ("Win rate", "win_rate", "{:.0%}"),
                      ("Max DD %", "max_dd_pct", "{:.1f}")]:
        if k is None:
            L.append(f"| {lab} | {evs['main']['oi_lag_min']} | {evs['conservative']['oi_lag_min']} |")
        else:
            L.append(f"| {lab} | {g(evs['main'], k, f)} | {g(evs['conservative'], k, f)} |")
    for lab, k in [("CI95 ขอบล่าง", "ci95_mean_net_pct"), ("Precision", "precision"), ("UNKNOWN share", "unknown_share"), ("เกณฑ์ที่เลือก", "chosen")]:
        def h(ev):
            x = ev.get(k)
            if x is None:
                return "-"
            if k == "ci95_mean_net_pct":
                return f"{x[0]:+.3f}"
            return f"{x:.1%}" if isinstance(x, float) else str(x)
        L.append(f"| {lab} | {h(evs['main'])} | {h(evs['conservative'])} |")
    L.append("\n> lag อนุรักษนิยมเป็นสมมติฐาน: latency ที่วัดได้วันนี้ไม่ได้พิสูจน์ว่า Binance เผยแพร่เร็วเท่ากันในอดีต")
    md = "\n".join(L)
    (REPORTS / "backtest_test.md").write_text(md, encoding="utf-8")
    (REPORTS / "backtest_test.json").write_text(json.dumps({"verdict": v, "evals": evs}, default=str, indent=2,
                                                           ensure_ascii=False), encoding="utf-8")
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
    lk = sp.add_parser("lock", help="ล็อก universe/splits/code/config/data ก่อนดูผล")
    lk.add_argument("--name", required=True)
    lk.add_argument("--train-start", required=True)
    lk.add_argument("--val-start", required=True)
    lk.add_argument("--test-start", required=True)
    lk.add_argument("--test-end", required=True)
    lk.add_argument("--design", default="ESPORTSUSDT", help="เหรียญที่ใช้ออกแบบสูตร (ไม่อยู่ใน universe)")
    lk.add_argument("--symbols", help="ค่าเริ่มต้น = ทุกเหรียญที่ดึงข้อมูลไว้")
    lk.add_argument("--note")
    b = sp.add_parser("backtest")
    b.add_argument("--manifest", required=True)
    b.add_argument("--open-test", action="store_true", help="เปิดชุด test (นับจริงแค่ครั้งแรก)")
    o = sp.add_parser("oi-latency")
    o.add_argument("--symbol", default="BTCUSDT")
    o.add_argument("--hours", type=float, default=48)
    o.add_argument("--every", type=int, default=15)
    o2 = sp.add_parser("oi-latency-summary")
    o2.add_argument("--symbol", default="BTCUSDT")
    a = ap.parse_args()
    {"selftest": cmd_selftest, "fetch": cmd_fetch, "forensic": cmd_forensic,
     "universe": cmd_universe, "backtest": cmd_backtest, "lock": cmd_lock, "oi-latency": cmd_oi_latency,
     "oi-latency-summary": lambda x: oi_latency_summary(x.symbol)}[a.cmd](a)


if __name__ == "__main__":
    main()
