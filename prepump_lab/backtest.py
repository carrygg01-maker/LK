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


def prepare(symbols: list[str], loader, cfg: Cfg) -> dict:
    """features + labels ต่อเหรียญ (labels ใช้แค่ประเมิน)"""
    out = {}
    for s in symbols:
        try:
            raw = loader(s)
            b = features.build(raw, cfg)
        except Exception as e:  # noqa: BLE001
            print(f"  ข้าม {s}: {e}")
            continue
        out[s] = {"bars": b, "eps": labels.episodes(b, cfg)}
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


def evaluate(prep: dict, oos: pd.DataFrame, cfg: Cfg) -> dict:
    if not len(oos):
        return {"verdict": "ไม่มีเทรด OOS — ข้อมูลไม่พอ", "pass": False}
    s = sim.summarize(oos.sort_values("entry_time"))
    lo, hi = cluster_bootstrap(oos)
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
    return {"summary": s, "ci95_mean_net_pct": [lo, hi], "random": rs,
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
