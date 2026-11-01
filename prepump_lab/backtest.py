"""
backtest.py — Phase 2 + 3: หลายเหรียญ + Walk-Forward

กันการหลอกตัวเอง:
  1. Universe = ทุกเหรียญที่มีข้อมูลในช่วงนั้น (รวมที่ไม่พุ่ง / ที่ delist แล้ว) ไม่ใช่เลือกเฉพาะเหรียญที่รู้ว่าพุ่ง
  2. Walk-forward รายเดือน: เลือกเกณฑ์จากเดือนก่อนหน้าเท่านั้น ทดสอบเดือนถัดไป รายงานเฉพาะ OOS
  3. เทรดใน train ต้องปิดก่อนเริ่ม test (กันข้อมูลรั่วข้ามขอบ)
  4. เทียบกับการสุ่มเข้าเทรดจำนวนเท่ากันในเหรียญ/เดือนเดียวกัน
  5. CI จาก bootstrap แบบ cluster รายวัน (เหรียญพุ่งพร้อมกันในวันเดียวกัน = ไม่อิสระ)
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

import features
import labels
import signals
import sim
from config import Cfg, WF_GRID

# เกณฑ์ผ่าน — ตั้งก่อนเห็นผล ห้ามขยับทีหลัง
PASS_MIN_TRADES = 100
PASS_MIN_PRECISION = 0.20      # alert ที่นำหน้าการพุ่งจริงอย่างน้อย 20% (ไม่พอลำพัง — ต้องผ่านข้ออื่นด้วย)
PASS_MIN_PF = 1.3              # Profit Factor หลังต้นทุน
PASS_MIN_POS_FOLDS = 0.6       # อย่างน้อย 60% ของเดือนทดสอบต้องกำไร (กันผลมาจากเดือนเดียว)

MIN_TRAIN_TRADES = 30


def _combos() -> list[dict]:
    keys = list(WF_GRID)
    return [dict(zip(keys, v)) for v in itertools.product(*[WF_GRID[k] for k in keys])]


KEEP_COLS = ["open", "high", "low", "close", "oi_chg", "fut_cvd_z", "spot_cvd_z", "rvol", "ret_ext", "spot_qv"]


def prepare(symbols: list[str], loader, cfg: Cfg, cut_before: str | None = None) -> dict:
    """
    features + labels ต่อเหรียญ (labels ใช้แค่ประเมิน)
    cut_before: ตัดข้อมูลดิบตาม 'เวลาเผยแพร่' ก่อนสร้าง feature -> ช่วง test ไม่ถูกแตะเลยแม้แต่ไบต์เดียว
    เก็บเฉพาะคอลัมน์ที่ใช้เป็น float32 เพื่อให้ 100+ เหรียญพอดี RAM 16GB
    """
    out = {}
    cut = pd.Timestamp(cut_before, tz="UTC") if cut_before else None
    for s in symbols:
        try:
            raw = loader(s)
            if cut is not None:
                raw = features.visible_raw(raw, cut, cfg)
            b = features.build(raw, cfg)
        except Exception as e:  # noqa: BLE001
            print(f"  ข้าม {s}: {e}")
            continue
        has_spot = bool(b["spot_qv"].notna().any())
        unk_state = signals.alert_state(signals.components(b, cfg), signals.unknown(b), cfg, has_spot) == "UNKNOWN"
        eps = labels.episodes(b, cfg)
        out[s] = {"bars": b[KEEP_COLS].astype("float32"), "eps": eps, "unknown": unk_state.to_numpy()}
    return out


def _run_combo(prep: dict, cfg: Cfg) -> pd.DataFrame:
    rows = []
    for s, d in prep.items():
        b = d["bars"]
        comp = signals.components(b, cfg)
        al = signals.alerts(comp, cfg)
        cls = labels.classify(al, d["eps"], cfg).set_index("time") if len(al) else pd.DataFrame()
        tr = sim.run(b, al, cfg)
        if len(tr):
            tr["symbol"] = s
            tr["kind"] = cls.loc[tr["alert"], "kind"].values
            tr["lead_min"] = cls.loc[tr["alert"], "lead_min"].values
            rows.append(tr)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _alerts_all(prep: dict, cfg: Cfg) -> pd.DataFrame:
    rows = []
    for s, d in prep.items():
        comp = signals.components(d["bars"], cfg)
        al = signals.alerts(comp, cfg)
        c = labels.classify(al, d["eps"], cfg)
        c["symbol"] = s
        rows.append(c)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def cluster_bootstrap(trades: pd.DataFrame, n_boot: int = 2000, seed: int = 3) -> tuple[float, float]:
    if len(trades) < 20:
        return (np.nan, np.nan)
    day = trades["entry_time"].dt.floor("D")
    groups = [g["net_pct"].values for _, g in trades.groupby(day)]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        x = np.concatenate([groups[i] for i in pick])
        means.append(x.mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def block_bootstrap(trades: pd.DataFrame, n_boot: int = 2000, seed: int = 4) -> tuple[float, float]:
    """moving-block bootstrap บนเทรดเรียงตามเวลา (block ≈ n^(1/3)) — เก็บ autocorrelation ระหว่างไม้ติดกัน"""
    x = trades.sort_values("entry_time")["net_pct"].to_numpy()
    n = len(x)
    if n < 20:
        return (np.nan, np.nan)
    blk = max(2, int(round(n ** (1 / 3))))
    starts = np.arange(n - blk + 1)
    k = int(np.ceil(n / blk))
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for i in range(n_boot):
        st = rng.choice(starts, k)
        means[i] = np.concatenate([x[s:s + blk] for s in st])[:n].mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def random_trades(prep: dict, test_trades: pd.DataFrame, cfg: Cfg, seed: int) -> pd.DataFrame:
    """สุ่มเวลาเข้า จำนวนเท่ากับเทรดจริงในแต่ละ (เหรียญ, เดือน)"""
    rng = np.random.default_rng(seed)
    rows = []
    if not len(test_trades):
        return pd.DataFrame()
    key = test_trades["entry_time"].dt.strftime("%Y-%m")
    for (s, m), g in test_trades.groupby([test_trades["symbol"], key]):
        b = prep[s]["bars"]
        pool = b.index[(b.index.strftime("%Y-%m") == m)]
        pool = pool[pool >= b.index[0] + pd.Timedelta(minutes=cfg.signal.baseline)]
        if len(pool) < 3:
            continue
        ts = pd.DatetimeIndex(np.sort(rng.choice(pool[:-2], size=min(len(g), len(pool) - 2), replace=False)))
        tr = sim.run(b, ts, cfg, no_overlap=False)
        if len(tr):
            tr["symbol"] = s
            rows.append(tr)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def walk_forward(prep: dict, cfg: Cfg, min_train_months: int = 2) -> dict:
    combos = _combos()
    print(f"  grid {len(combos)} ชุด × {len(prep)} เหรียญ ...")
    per_combo = {}
    for i, c in enumerate(combos):
        per_combo[i] = _run_combo(prep, cfg.with_signal(**c))
    all_idx = pd.DatetimeIndex(sorted(set().union(*[d["bars"].index for d in prep.values()])))
    months = sorted(set(all_idx.strftime("%Y-%m")))
    folds, oos = [], []
    for k in range(min_train_months, len(months)):
        m = months[k]
        t_start = pd.Timestamp(m + "-01", tz="UTC")
        t_end = t_start + pd.offsets.MonthBegin(1)
        best_i, best_score, best_n = None, -np.inf, 0
        for i, tr in per_combo.items():
            if not len(tr):
                continue
            trn = tr[tr["exit_time"] < t_start]
            if len(trn) < MIN_TRAIN_TRADES:
                continue
            sc = trn["net_pct"].mean()
            if sc > best_score:
                best_i, best_score, best_n = i, sc, len(trn)
        if best_i is None:
            folds.append({"month": m, "chosen": "ไม่มีชุดไหนมีเทรดพอใน train — ข้าม", "test_n": 0})
            continue
        tr = per_combo[best_i]
        tst = tr[(tr["entry_time"] >= t_start) & (tr["entry_time"] < t_end)].copy()
        tst["fold"] = m
        oos.append(tst)
        folds.append({"month": m, "chosen": combos[best_i], "train_n": best_n,
                      "train_mean_net_pct": round(best_score, 3), "test_n": int(len(tst)),
                      "test_mean_net_pct": round(float(tst["net_pct"].mean()), 3) if len(tst) else None})
    oos_df = pd.concat(oos, ignore_index=True) if oos else pd.DataFrame()
    return {"folds": folds, "oos": oos_df, "combos": combos}


def final_test(prep: dict, cfg: Cfg, train_start: str, test_start: str, test_end: str) -> dict:
    """
    เลือกเกณฑ์ครั้งเดียวจาก train+validation (เทรดต้องปิดก่อน test_start) แล้วทดสอบช่วง test ครั้งเดียว
    """
    combos = _combos()
    t0, ts = pd.Timestamp(train_start, tz="UTC"), pd.Timestamp(test_start, tz="UTC")
    te = pd.Timestamp(test_end, tz="UTC") + pd.Timedelta(days=1)
    best_i, best, best_n, per = None, -np.inf, 0, {}
    for i, c in enumerate(combos):
        tr = _run_combo(prep, cfg.with_signal(**c))
        per[i] = tr
        if not len(tr):
            continue
        dev = tr[(tr["entry_time"] >= t0) & (tr["exit_time"] < ts)]
        if len(dev) >= MIN_TRAIN_TRADES and dev["net_pct"].mean() > best:
            best_i, best, best_n = i, dev["net_pct"].mean(), len(dev)
    if best_i is None:
        return {"chosen": None, "oos": pd.DataFrame()}
    tr = per[best_i]
    oos = tr[(tr["entry_time"] >= ts) & (tr["entry_time"] < te)].copy()
    oos["fold"] = oos["entry_time"].dt.strftime("%Y-%m")
    # UNKNOWN share ในช่วง test
    tot, unk = 0, 0
    for d in prep.values():
        idx = d["bars"].index
        m = (idx >= ts) & (idx < te)
        tot += int(m.sum())
        unk += int(d["unknown"][m].sum())
    return {"chosen": combos[best_i], "dev_n": best_n, "dev_mean_net_pct": float(best), "oos": oos,
            "unknown_share": unk / tot if tot else None}


INCONCLUSIVE_MAX_UNKNOWN = 0.05   # นาทีที่ alert = UNKNOWN เกิน 5% ของช่วง test -> พิสูจน์ไม่ได้
INCONCLUSIVE_MIN_READY = 0.80     # เหรียญใน universe ที่ข้อมูล ready ต้อง >= 80%


def three_way(ev_main: dict, ev_cons: dict, ctx: dict) -> dict:
    """PASS / FAIL / INCONCLUSIVE — INCONCLUSIVE ตัดสินก่อนเสมอ"""
    inc = []
    if ctx.get("drift"):
        inc.append("โค้ด/เกณฑ์/ข้อมูลเปลี่ยนหลังล็อก manifest: " + "; ".join(ctx["drift"]))
    if ctx.get("prior_test_runs", 0) > 0:
        inc.append(f"ชุด test ถูกเปิดไปแล้ว {ctx['prior_test_runs']} ครั้ง — ไม่ใช่การทดสอบแบบปิดตา")
    if ctx.get("ready_share") is not None and ctx["ready_share"] < INCONCLUSIVE_MIN_READY:
        inc.append(f"เหรียญที่ข้อมูลพร้อม {ctx['ready_share']:.0%} < {INCONCLUSIVE_MIN_READY:.0%}")
    for tag, ev in (("lag ปกติ", ev_main), ("lag อนุรักษนิยม", ev_cons)):
        u = ev.get("unknown_share")
        if u is not None and u > INCONCLUSIVE_MAX_UNKNOWN:
            inc.append(f"[{tag}] นาทีที่ alert = UNKNOWN {u:.1%} > {INCONCLUSIVE_MAX_UNKNOWN:.0%}")
        n = (ev.get("summary") or {}).get("n", 0)
        if n < PASS_MIN_TRADES:
            inc.append(f"[{tag}] เทรด OOS {n} < {PASS_MIN_TRADES} — n น้อยเกินจะสรุปได้")
    if inc:
        return {"verdict": "INCONCLUSIVE", "why": inc}
    fails = [f"[{tag}] {k}" for tag, ev in (("lag ปกติ", ev_main), ("lag อนุรักษนิยม", ev_cons))
             for k, v in ev.get("checks", {}).items() if not v]
    if fails:
        return {"verdict": "FAIL", "why": fails}
    return {"verdict": "PASS", "why": ["ผ่านทุกเกณฑ์ที่ล็อกไว้ ทั้ง lag ปกติและอนุรักษนิยม ในชุด test ที่เปิดครั้งแรก"]}


def evaluate(prep: dict, oos: pd.DataFrame, cfg: Cfg) -> dict:
    if not len(oos):
        return {"verdict": "ไม่มีเทรด OOS — ข้อมูลไม่พอ", "pass": False}
    s = sim.summarize(oos.sort_values("entry_time"))
    c_lo, c_hi = cluster_bootstrap(oos)
    b_lo, b_hi = block_bootstrap(oos)
    # ใช้ขอบล่างที่ระวังที่สุดในสองวิธี
    lo = np.nanmin([c_lo, b_lo]) if np.isfinite([c_lo, b_lo]).any() else np.nan
    hi = np.nanmax([c_hi, b_hi]) if np.isfinite([c_hi, b_hi]).any() else np.nan
    rnd = random_trades(prep, oos, cfg, seed=5)
    rs = sim.summarize(rnd) if len(rnd) else {"n": 0}
    kinds = oos["kind"].value_counts(normalize=True).to_dict()
    fold_mean = oos.groupby("fold")["net_pct"].mean()
    pos_folds = float((fold_mean > 0).mean()) if len(fold_mean) else 0.0
    xs = np.sort(oos["net_pct"].values)
    trimmed = float(xs[: max(1, int(len(xs) * 0.95))].mean())
    precision = kinds.get("lead", 0.0)
    # recall: การพุ่งใน OOS ที่มี alert นำหน้า (นับจากเทรด — alert ที่ถูกข้ามเพราะถือไม้อยู่ไม่นับ)
    test_months = set(oos["fold"])
    n_eps, hit = 0, 0
    for sym, d in prep.items():
        for i, e in enumerate(d["eps"]):
            if e.breakout_time.strftime("%Y-%m") in test_months:
                n_eps += 1
                if ((oos["symbol"] == sym) & (oos["kind"] == "lead")).any():
                    sub = oos[(oos["symbol"] == sym) & (oos["kind"] == "lead")]
                    lw = pd.Timedelta(minutes=cfg.label.lead_window)
                    if ((sub["alert"] >= e.breakout_time - lw) & (sub["alert"] < e.breakout_time)).any():
                        hit += 1
    lead = oos.loc[oos["kind"] == "lead", "lead_min"]
    checks = {
        f"n OOS ≥ {PASS_MIN_TRADES}": s["n"] >= PASS_MIN_TRADES,
        "ขอบล่าง CI 95% ของกำไรเฉลี่ย > 0": bool(np.isfinite(lo) and lo > 0),
        "ชนะการสุ่มเข้าเทรด": bool(rs.get("n") and s["mean_net_pct"] > rs["mean_net_pct"]),
        f"Precision ≥ {PASS_MIN_PRECISION:.0%}": precision >= PASS_MIN_PRECISION,
        f"Profit Factor ≥ {PASS_MIN_PF}": s["profit_factor"] >= PASS_MIN_PF,
        f"เดือนทดสอบที่กำไร ≥ {PASS_MIN_POS_FOLDS:.0%}": pos_folds >= PASS_MIN_POS_FOLDS,
        "ตัด 5% ไม้ที่ดีที่สุดออกแล้วยังบวก": trimmed > 0,
    }
    ok = all(checks.values())
    return {"summary": s, "ci95_mean_net_pct": [lo, hi],
            "ci95_cluster_day": [c_lo, c_hi], "ci95_block": [b_lo, b_hi], "random": rs,
            "precision": precision, "late_share": kinds.get("late", 0.0),
            "false_share": kinds.get("false", 0.0),
            "recall": hit / n_eps if n_eps else None, "n_pumps_oos": n_eps,
            "median_lead_min": float(lead.median()) if len(lead) else None,
            "positive_fold_share": pos_folds, "mean_net_pct_trim_top5": trimmed,
            "checks": checks, "pass": ok,
            "verdict": "ผ่าน" if ok else "ไม่ผ่าน"}


def failure_analysis(oos: pd.DataFrame) -> list[dict]:
    """false alert แพ้เพราะอะไร: แยกตามเหตุผลการออก + MFE (เคยกำไรแล้วคืนไหม)"""
    f = oos[oos["kind"] != "lead"]
    if not len(f):
        return []
    out = []
    for r, g in f.groupby("reason"):
        out.append({"exit_reason": r, "n": int(len(g)), "mean_net_pct": round(g["net_pct"].mean(), 3),
                    "median_mfe_pct": round(g["mfe_pct"].median(), 2),
                    "share_mfe_over_3pct": round(float((g["mfe_pct"] > 3).mean()), 3)})
    return out


def to_markdown(res: dict, ev: dict, fa: list[dict], cfg: Cfg, n_symbols: int) -> str:
    L = ["# Walk-Forward Backtest — Pre-Pump Signal\n"]
    L.append(f"Universe: {n_symbols} เหรียญ · รายงานเฉพาะ Out-of-Sample\n")
    L.append(f"## Final Verdict: **{ev['verdict']}**\n")
    if "checks" in ev:
        L.append("| เกณฑ์ (ตั้งก่อนรัน) | ผล |\n|---|---|")
        for k, v in ev["checks"].items():
            L.append(f"| {k} | {'✅' if v else '❌'} |")
        s = ev["summary"]
        lo, hi = ev["ci95_mean_net_pct"]
        L.append(f"\n- เทรด OOS {s['n']} ไม้ · win {s['win_rate']*100:.0f}% · Expectancy **{s['expectancy_pct']:+.3f}%/ไม้** "
                 f"(CI95 {lo:+.3f} … {hi:+.3f}) · Profit Factor {s['profit_factor']:.2f} · Max DD {s['max_dd_pct']:.1f}%")
        be = s.get("breakeven_win_rate")
        L.append(f"- กำไรเฉลี่ยต่อไม้ชนะ {s['avg_win_pct']:+.2f}% · ขาดทุนเฉลี่ยต่อไม้แพ้ {s['avg_loss_pct']:+.2f}% · "
                 f"payoff {s['payoff_ratio']:.2f} · win rate คุ้มทุน {be*100:.0f}%" if be is not None else "- payoff: -")
        L.append(f"- เดือนทดสอบที่กำไร {ev['positive_fold_share']*100:.0f}% · Expectancy เมื่อตัด 5% ไม้ดีสุด {ev['mean_net_pct_trim_top5']:+.3f}%")
        L.append(f"- CI95 แยกวิธี: cluster รายวัน {ev['ci95_cluster_day'][0]:+.3f} … {ev['ci95_cluster_day'][1]:+.3f} · "
                 f"moving-block {ev['ci95_block'][0]:+.3f} … {ev['ci95_block'][1]:+.3f} (ตัดสินด้วยขอบล่างที่ต่ำกว่า)")
        r = ev["random"]
        if r.get("n"):
            L.append(f"- สุ่มเข้าเทรดจำนวนเท่ากัน: เฉลี่ยสุทธิ {r['mean_net_pct']:+.3f}%")
        L.append(f"- Precision (alert นำหน้าการพุ่งจริง): {ev['precision']*100:.1f}% · "
                 f"Late: {ev['late_share']*100:.1f}% · False: {ev['false_share']*100:.1f}%")
        rc = ev.get("recall")
        L.append(f"- Recall: {rc*100:.1f}% ของ {ev['n_pumps_oos']} การพุ่งใน OOS" if rc is not None else "- Recall: -")
        if ev.get("median_lead_min") is not None:
            L.append(f"- Lead time มัธยฐาน: {ev['median_lead_min']:.0f} นาที")
    L.append("\n## Walk-forward folds\n\n| เดือนทดสอบ | เกณฑ์ที่เลือกจากอดีต | train n | train เฉลี่ย% | test n | test เฉลี่ย% |\n|---|---|---|---|---|---|")
    for f in res["folds"]:
        L.append(f"| {f['month']} | {f['chosen']} | {f.get('train_n','-')} | {f.get('train_mean_net_pct','-')} | "
                 f"{f['test_n']} | {f.get('test_mean_net_pct','-')} |")
    L.append("\n> ถ้า train ดีแต่ test แย่ทุก fold = overfit ไม่ใช่โชคร้าย\n")
    if fa:
        L.append("## Failure Analysis (alert ที่ไม่ได้นำหน้าการพุ่ง)\n\n| ออกเพราะ | n | เฉลี่ย% | MFE มัธยฐาน% | สัดส่วนที่เคยบวก >3% |\n|---|---|---|---|---|")
        for r in fa:
            L.append(f"| {r['exit_reason']} | {r['n']} | {r['mean_net_pct']} | {r['median_mfe_pct']} | {r['share_mfe_over_3pct']} |")
        L.append("\n> ถ้า false alert ส่วนใหญ่เคยบวก >3% ก่อนโดน stop = ปัญหาอยู่ที่ exit ไม่ใช่ entry\n")
    return "\n".join(L)
