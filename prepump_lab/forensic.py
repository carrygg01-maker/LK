"""
forensic.py — Phase 1: Forensic Replay ของเหรียญเดียว

ตอบ 4 ข้อที่ต้องได้ก่อนลงทุนต่อ:
  Q1 สัญญาณแรกเกิดเมื่อไร
  Q2 เกิดก่อนราคา breakout กี่นาที
  Q3 มี false signal ก่อนหน้านั้นกี่ครั้ง
  Q4 ถ้าเข้าเทรดตามสัญญาณจริง (ราคาที่เข้าได้จริง + ต้นทุน) ผลเป็นอย่างไร
และแนบ: ลำดับว่า data stream ไหนขยับก่อน (OI / CVD / Volume / Price) + baseline สุ่ม
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import features
import labels
import signals
import sim
from config import Cfg


def first_mover(b: pd.DataFrame, comp: pd.DataFrame, ep: labels.Episode, lookback_min: int = 720) -> list[dict]:
    """แต่ละเงื่อนไข: ครั้งแรกที่เริ่ม 'ติดต่อเนื่อง' ก่อน breakout และอัตราติดสัญญาณปกติ (ต่อวัน) ช่วงอื่น"""
    t0 = ep.breakout_time - pd.Timedelta(minutes=lookback_min)
    win = comp.loc[t0: ep.breakout_time]
    outside = comp.loc[: t0 - pd.Timedelta(minutes=1)]
    days_out = max(len(outside) / 1440, 1e-9)
    rows = []
    for c in signals.COMPONENTS:
        if c == "not_extended":
            continue
        s = win[c]
        fired = s.index[s.values]
        first = fired[0] if len(fired) else None
        # จำนวนครั้งที่เงื่อนไขนี้ "เริ่มติด" ในช่วงก่อนหน้า (นับขอบขาขึ้น)
        o = outside[c].astype(int)
        rises = int(((o.diff() == 1)).sum()) if len(o) else 0
        rows.append({
            "component": signals.LABELS_TH[c],
            "first_fire": str(first) if first is not None else "-",
            "lead_min": round((ep.breakout_time - first).total_seconds() / 60) if first is not None else None,
            "baseline_fires_per_day": round(rises / days_out, 2) if len(outside) else None,
        })
    # ราคาเอง: ครั้งแรกที่ ret_ext >= 2% ก่อน breakout (ราคาเริ่มขยับ)
    pr = b.loc[t0: ep.breakout_time, "ret_ext"]
    pf = pr.index[(pr >= 0.02).values]
    po = (b.loc[outside.index, "ret_ext"] >= 0.02).astype(int) if len(outside) else pd.Series(dtype=int)
    rows.append({"component": "Price +2%/60m", "first_fire": str(pf[0]) if len(pf) else "-",
                 "lead_min": round((ep.breakout_time - pf[0]).total_seconds() / 60) if len(pf) else None,
                 "baseline_fires_per_day": round(int((po.diff() == 1).sum()) / days_out, 2) if len(po) else None})
    rows.sort(key=lambda r: -(r["lead_min"] if r["lead_min"] is not None else -1e9))
    return rows


def random_baseline(b: pd.DataFrame, n: int, cfg: Cfg, n_sims: int = 500, seed: int = 11,
                    warmup: int | None = None) -> dict:
    """เข้าเทรดแบบสุ่มจำนวนเท่ากัน — ถ้าสัญญาณไม่ชนะการสุ่ม แปลว่าไม่มีข้อมูลอะไร"""
    if n == 0:
        return {}
    rng = np.random.default_rng(seed)
    w = warmup if warmup is not None else cfg.signal.baseline
    pool = b.index[w: len(b) - 2]
    means = []
    for _ in range(n_sims):
        ts = pd.DatetimeIndex(np.sort(rng.choice(pool, size=min(n, len(pool)), replace=False)))
        tr = sim.run(b, ts, cfg, no_overlap=False)
        means.append(tr["net_pct"].mean() if len(tr) else np.nan)
    means = np.array(means)
    means = means[np.isfinite(means)]
    return {"sims": int(len(means)), "mean_net_pct_p50": float(np.median(means)),
            "p05": float(np.percentile(means, 5)), "p95": float(np.percentile(means, 95)),
            "_dist": means}


def run(raw: dict[str, pd.DataFrame], cfg: Cfg, focus_start: str | None = None,
        focus_end: str | None = None, symbol: str = "") -> dict:
    b = features.build(raw, cfg)
    comp = signals.components(b, cfg)
    unk = signals.unknown(b)
    has_spot = bool(b["spot_qv"].notna().any())
    state = signals.alert_state(comp, unk, cfg, has_spot)
    al = signals.alerts(comp, cfg)
    eps = labels.episodes(b, cfg)
    cls = labels.classify(al, eps, cfg)
    trades = sim.run(b, al, cfg)

    fs = pd.Timestamp(focus_start, tz="UTC") if focus_start else b.index[0]
    fe = pd.Timestamp(focus_end, tz="UTC") + pd.Timedelta(days=1) if focus_end else b.index[-1]
    focus_eps = [e for e in eps if fs <= e.breakout_time <= fe]
    main = max(focus_eps, key=lambda e: e.gain) if focus_eps else None

    ans: dict = {"symbol": symbol, "data_start": str(b.index[0]), "data_end": str(b.index[-1]),
                 "n_alerts": int(len(al)), "episodes": [e.as_dict() for e in eps],
                 "main_episode": main.as_dict() if main else None}
    warm_end = b.index[0] + pd.Timedelta(minutes=cfg.signal.baseline)
    ans["warmup_end"] = str(warm_end)
    ans["data_coverage"] = {
        "spot": bool(b["spot_qv"].notna().any()),
        "oi": bool(b["oi"].notna().any()),
        "funding": bool(b["funding"].notna().any()),
        "aggtrades": bool(b["fut_big_delta"].notna().any()),
    }

    if main is not None:
        lw = pd.Timedelta(minutes=cfg.label.lead_window)
        lead = cls[(cls["kind"] == "lead") & (cls["episode"] == eps.index(main))]
        before = cls[(cls["time"] < main.breakout_time - lw) & (cls["time"] >= warm_end)]
        first = lead["time"].min() if len(lead) else None
        ans["Q1_first_signal"] = str(first) if first is not None else None
        ans["Q2_lead_min"] = float((main.breakout_time - first).total_seconds() / 60) if first is not None else None
        ans["Q3_false_before"] = int((before["kind"] == "false").sum())
        ans["Q3_days_observed"] = round((main.breakout_time - lw - warm_end).total_seconds() / 86400, 2)
        tr_main = trades[trades["alert"] == first] if first is not None and len(trades) else pd.DataFrame()
        ans["Q4_trade_on_first_signal"] = tr_main.iloc[0].to_dict() if len(tr_main) else None
        ans["first_mover"] = first_mover(b, comp, main)
        late = cls[(cls["kind"] == "late") & (cls["episode"] == eps.index(main))]
        ans["late_alerts"] = int(len(late))
        if first is not None:
            ans["evidence_at_first_signal"] = evidence(b, comp, first)
            ans["price_at_signal"] = float(b.loc[first, "close"])
            ans["signal_drivers"] = drivers(comp, first)
        ans["near_miss"] = near_miss(b, main, cfg)
        lb = state.loc[main.breakout_time - lw: main.breakout_time]
        ans["lead_window_unknown_min"] = int((lb == "UNKNOWN").sum())
        ans["lead_window_unknown_by_feature"] = {signals.LABELS_TH[k]: int(v) for k, v in
                                                 unk.loc[lb.index].sum().items() if v}
    ans["all_trades_summary"] = sim.summarize(trades)
    rb = random_baseline(b, len(trades), cfg)
    ans["random_baseline"] = {k: v for k, v in rb.items() if not k.startswith("_")}
    ans["classification_counts"] = cls["kind"].value_counts().to_dict() if len(cls) else {}
    return {"answers": ans, "bars": b, "components": comp, "state": state, "alerts": al, "classes": cls,
            "episodes": eps, "trades": trades}


def drivers(comp: pd.DataFrame, t: pd.Timestamp) -> dict:
    """เงื่อนไขไหนผ่าน ณ alert และตัวไหน 'ติดเป็นตัวสุดท้าย' (= ตัวจุดชนวน alert)"""
    passed = [signals.LABELS_TH[c] for c in signals.COMPONENTS if comp.at[t, c]]
    onset = {}
    for c in ["oi_expansion", "fut_cvd", "spot_cvd", "rel_volume"]:
        s = comp[c].loc[:t]
        if not s.iloc[-1]:
            continue
        off = s.index[~s.values]
        onset[signals.LABELS_TH[c]] = str(off[-1] + pd.Timedelta(minutes=1)) if len(off) else str(s.index[0])
    trigger = max(onset, key=lambda k: onset[k]) if onset else None
    return {"passed": passed, "onset": onset, "trigger": trigger}


def near_miss(b: pd.DataFrame, ep: labels.Episode, cfg: Cfg) -> list[dict]:
    """
    ถ้าไม่มี alert: ใน 12 ชม.ก่อน breakout แต่ละเงื่อนไขเข้าใกล้เกณฑ์แค่ไหน
    ใช้แยกว่าล้มเหลวเพราะ 'ข้อมูลไม่มี' / 'เกณฑ์เข้มไป' / 'ตลาดไม่ได้ส่งสัญญาณแบบนี้'
    """
    s = cfg.signal
    w = b.loc[ep.breakout_time - pd.Timedelta(minutes=cfg.label.lead_window): ep.breakout_time]
    out = []
    MIN_COV = 0.8
    for name, col, th, scale in [("OI Δ60m", "oi_chg", s.oi_min_chg, 100), ("Futures CVD z", "fut_cvd_z", s.cvd_z, 1),
                                 ("Spot CVD z", "spot_cvd_z", s.cvd_z, 1), ("Relative Volume", "rvol", s.rv_min, 1)]:
        x = w[col]
        cov = float(x.notna().mean()) if len(x) else 0.0
        if x.notna().sum() == 0:
            out.append({"metric": name, "max": None, "threshold": th * scale, "coverage": 0.0,
                        "status": "ข้อมูลขาด", "cause": "data"})
            continue
        mx = float(x.max())
        if mx >= th:
            st, cause = "ถึงเกณฑ์", "ok"
        elif cov < MIN_COV:
            st, cause = f"ข้อมูลขาด (มี {cov:.0%}) — สรุปไม่ได้", "data"
        elif mx >= 0.7 * th:
            st, cause = "เฉียดเกณฑ์ (≥70%)", "threshold"
        else:
            st, cause = "ห่างเกณฑ์ — ไม่มีแรงแบบที่สูตรมองหา", "market"
        out.append({"metric": name, "max": round(mx * scale, 2), "threshold": th * scale,
                    "coverage": round(cov, 3), "at": str(x.idxmax()), "status": st, "cause": cause})
    return out


def lag_sensitivity(raw: dict, cfg: Cfg, focus_start, focus_end, lags=None) -> list[dict]:
    """ผลขึ้นกับสมมติฐานเวลาเผยแพร่ OI แค่ไหน — ถ้า lag 0 ดีกว่า 15 มาก = ผลพึ่งการเห็น OI เร็ว"""
    from dataclasses import replace
    lags = lags or sorted({0, 5, 10, 15, 30, cfg.data.oi_lag_min, cfg.data.oi_lag_conservative_min})
    rows = []
    for lag in lags:
        c = replace(cfg, data=replace(cfg.data, oi_lag_min=lag))
        a = run(raw, c, focus_start, focus_end)["answers"]
        t = a.get("Q4_trade_on_first_signal") or {}
        s = a["all_trades_summary"]
        rows.append({"oi_lag_min": lag, "first_signal": a.get("Q1_first_signal"), "lead_min": a.get("Q2_lead_min"),
                     "false_before": a.get("Q3_false_before"), "first_trade_net_pct": t.get("net_pct"),
                     "all_n": s.get("n", 0), "all_expectancy_pct": s.get("expectancy_pct")})
    return rows


def verdict(a: dict, cfg: Cfg) -> dict:
    """
    PASS / FAIL / INCONCLUSIVE สำหรับเหรียญเดียว (PASS = เฉพาะกรณีนี้ ยังไม่ใช่หลักฐานของกลยุทธ์)
    INCONCLUSIVE มาก่อนเสมอ: ถ้าข้อมูลไม่พอ ห้ามสรุปว่าสูตรล้มเหลว
    """
    why = []
    if not a.get("main_episode"):
        return {"verdict": "INCONCLUSIVE", "why": ["ไม่พบการพุ่งตามนิยามในช่วงที่โฟกัส — ตรวจนิยาม/ช่วงเวลา"]}
    dq = a.get("data_quality") or {}
    if dq.get("status") == "not_ready":
        why.append("ข้อมูลทั้งช่วงไม่พร้อม: " + "; ".join(dq.get("reasons", [])))
    unk = a.get("lead_window_unknown_min", 0)
    if unk and not a.get("Q1_first_signal"):
        why.append(f"ใน 12 ชม.ก่อน breakout มี {unk} นาทีที่สถานะ alert = UNKNOWN "
                   f"(ข้อมูลขาด: {a.get('lead_window_unknown_by_feature')}) — อาจมีสัญญาณที่มองไม่เห็น")
    if any(r.get("cause") == "data" for r in a.get("near_miss", [])) and not a.get("Q1_first_signal"):
        why.append("เงื่อนไขบางข้อขาดข้อมูลในช่วง lookback")
    if why:
        return {"verdict": "INCONCLUSIVE", "why": why}
    if not a.get("Q1_first_signal"):
        return {"verdict": "FAIL", "why": ["ข้อมูลครบ แต่ไม่มี alert ก่อน breakout"]}
    cons = cfg.data.oi_lag_conservative_min
    ls = {r["oi_lag_min"]: r for r in a.get("lag_sensitivity", [])}
    if cons in ls and ls[cons]["lead_min"] is None:
        return {"verdict": "FAIL", "why": [f"สัญญาณหายเมื่อ OI lag = {cons} นาที (อนุรักษนิยม) — พึ่งการเห็น OI เร็ว"]}
    t = a.get("Q4_trade_on_first_signal")
    tc = ls.get(cons, {}).get("first_trade_net_pct")
    if not t or t["net_pct"] <= 0 or (tc is not None and tc <= 0):
        return {"verdict": "FAIL", "why": ["มีสัญญาณนำ แต่เทรดตามสัญญาณแรกไม่คุ้มต้นทุน (ที่ lag ปกติหรืออนุรักษนิยม)"]}
    if cons not in ls:
        return {"verdict": "INCONCLUSIVE", "why": ["ยังไม่ได้รันที่ lag อนุรักษนิยม"]}
    return {"verdict": "PASS", "why": ["มีสัญญาณนำและกำไรหลังต้นทุน ทั้งที่ lag ปกติและอนุรักษนิยม",
                                       "เป็นผลของเหรียญเดียวที่รู้อยู่แล้วว่าพุ่ง — ต้องผ่าน Out-of-Sample ก่อนนับเป็นหลักฐาน"]}


def evidence(b: pd.DataFrame, comp: pd.DataFrame, t: pd.Timestamp) -> list[dict]:
    r = b.loc[t]
    c = comp.loc[t]

    def fmt(v, kind):
        if pd.isna(v):
            return "ไม่มีข้อมูล"
        return {"pct": f"{v*100:+.1f}%", "z": f"{v:+.1f}σ", "x": f"{v:.1f}x"}[kind]
    return [
        {"name": "OI Expansion", "pass": bool(c["oi_expansion"]), "value": fmt(r["oi_chg"], "pct")},
        {"name": "Futures CVD", "pass": bool(c["fut_cvd"]), "value": fmt(r["fut_cvd_z"], "z")},
        {"name": "Spot CVD", "pass": bool(c["spot_cvd"]), "value": fmt(r["spot_cvd_z"], "z")},
        {"name": "Relative Volume", "pass": bool(c["rel_volume"]), "value": fmt(r["rvol"], "x")},
        {"name": "Price Extension", "pass": bool(c["not_extended"]), "value": fmt(r["ret_ext"], "pct")},
    ]


def to_markdown(ans: dict, cfg: Cfg) -> str:
    a = ans
    L = []
    L.append(f"# Forensic Replay — {a['symbol']}\n")
    L.append(f"ข้อมูล: {a['data_start']} → {a['data_end']} (UTC) · warm-up baseline ถึง {a['warmup_end']}\n")
    cov = a["data_coverage"]
    L.append("ข้อมูลที่มีจริง: " + ", ".join(f"{k}={'✅' if v else '❌'}" for k, v in cov.items()) + "\n")
    dq = a.get("data_quality")
    if dq:
        L.append(f"คุณภาพข้อมูล: **{dq['status']}** {'· ' + '; '.join(dq['reasons']) if dq['reasons'] else ''} "
                 f"{'· ' + '; '.join(dq['warnings']) if dq['warnings'] else ''} (รายละเอียด: reports/data_quality_report.json)\n")
    fc = a.get("feature_coverage_pre_breakout")
    if fc:
        L.append("ข้อมูลที่ใช้คำนวณได้ใน 12 ชม.ก่อน breakout: " + " · ".join(f"{k} {v:.0%}" for k, v in fc.items())
                 + (" ⚠️ ต่ำกว่า 80% — ผลช่วงนี้เชื่อถือได้น้อย" if min(fc.values()) < 0.8 else "") + "\n")
    me = a.get("main_episode")
    if not me:
        if a.get("verdict"):
            L.append(f"## Verdict: **{a['verdict']['verdict']}**\n")
        L.append("\n**ไม่พบการพุ่งที่เข้านิยามในช่วงที่โฟกัส** "
                 f"(นิยาม: low→peak ≥ {cfg.label.pump_gain*100:.0f}% ภายใน {cfg.label.pump_window//60} ชม.)\n")
        return "\n".join(L)
    L.append(f"\n## เหตุการณ์หลัก\n\nlow {me['low']:.6g} @ {me['low_time']} → breakout (+{cfg.label.breakout_gain*100:.0f}%) "
             f"@ **{me['breakout_time']}** → peak {me['peak']:.6g} @ {me['peak_time']} (**+{me['gain_pct']}%**)\n")
    vd = a.get("verdict")
    if vd:
        L.append(f"## Verdict (เหรียญนี้): **{vd['verdict']}**\n")
        L.extend(f"- {w}" for w in vd["why"])
        L.append("")
    if a.get("lead_window_unknown_min"):
        L.append(f"⚠️ ใน 12 ชม.ก่อน breakout มี {a['lead_window_unknown_min']} นาทีที่สถานะ alert = UNKNOWN "
                 f"(ข้อมูลขาด: {a.get('lead_window_unknown_by_feature')})\n")
    t4 = a.get("Q4_trade_on_first_signal")
    dr = a.get("signal_drivers") or {}
    oos = a.get("oos_summary")
    L.append("## Metrics สรุป\n")
    L.append("| Metric | ผล |\n|---|---|")
    L.append(f"| First Signal Time | {a.get('Q1_first_signal') or 'ไม่มี'} |")
    L.append(f"| Price at Signal | close แท่งสัญญาณ {a['price_at_signal']:.6g} · ราคาเข้าได้จริง (open แท่งถัดไป + slippage) {t4['entry']:.6g} |"
             if a.get("price_at_signal") is not None and t4 else "| Price at Signal | - |")
    L.append(f"| Breakout Time | {me['breakout_time']} |")
    q2m = a.get("Q2_lead_min")
    L.append(f"| Lead Time | {f'{q2m:.0f} นาที' if q2m is not None else '-'} |")
    L.append(f"| Signal Drivers | ผ่าน: {', '.join(dr.get('passed', [])) or '-'} · ตัวจุดชนวน: {dr.get('trigger') or '-'} |")
    L.append(f"| False Signals ก่อนพุ่ง | {a.get('Q3_false_before')} ครั้ง ใน {a.get('Q3_days_observed')} วัน |")
    L.append(f"| Net Return (สัญญาณแรก) | {t4['net_pct']:+.2f}% ({t4['reason']}) |" if t4 else "| Net Return (สัญญาณแรก) | - |")
    if oos:
        L.append(f"| Out-of-Sample | {oos} |")
    else:
        L.append("| Out-of-Sample | ยังไม่รัน — `python run.py backtest --exclude " + a["symbol"] + "` |")
    L.append("")
    if not a.get("Q1_first_signal") and a.get("near_miss"):
        L.append("## ทำไมไม่มีสัญญาณ (12 ชม.ก่อน breakout)\n")
        L.append("| เงื่อนไข | ค่าสูงสุด | เกณฑ์ | ข้อมูลมี | สถานะ |\n|---|---|---|---|---|")
        for r in a["near_miss"]:
            L.append(f"| {r['metric']} | {r['max'] if r['max'] is not None else '-'} | {r['threshold']} | "
                     f"{r['coverage']:.0%} | {r['status']} |")
        causes = {r["cause"] for r in a["near_miss"] if r["cause"] != "ok"}
        diag = ("**ข้อมูลไม่พอ — ยังสรุปไม่ได้ว่าสูตรล้มเหลว**" if "data" in causes else
                "**เกณฑ์อาจเข้มเกิน**" if "threshold" in causes else
                "**ตลาดไม่ได้ส่งสัญญาณแบบที่สูตรมองหา**" if "market" in causes else "ทุกเงื่อนไขถึงเกณฑ์แต่ไม่พร้อมกัน")
        L.append(f"\nสรุปสาเหตุ: {diag} (ลำดับความสำคัญ: ข้อมูลขาด > เกณฑ์ > ตลาด)")
        L.append("\n> ไม่มีข้อมูล = ปัญหาข้อมูล · เฉียด = เกณฑ์อาจเข้มไป (แต่ห้ามลดเกณฑ์จากเหรียญนี้เหรียญเดียว) · ห่างเกณฑ์ = ตลาดไม่ได้ส่งสัญญาณแบบที่สูตรมองหา\n")
    if a.get("lag_sensitivity"):
        L.append("## ความไวต่อสมมติฐานเวลาเผยแพร่ OI\n")
        L.append("| OI lag (นาที) | สัญญาณแรก | lead (นาที) | false ก่อน | สุทธิไม้แรก % | ทุก alert n | expectancy % |\n|---|---|---|---|---|---|---|")
        for r in a["lag_sensitivity"]:
            f = lambda v, d=2: "-" if v is None or (isinstance(v, float) and np.isnan(v)) else (f"{v:.{d}f}" if isinstance(v, float) else str(v))  # noqa: E731
            L.append(f"| {r['oi_lag_min']} | {r['first_signal'] or '-'} | {f(r['lead_min'], 0)} | {r['false_before']} | "
                     f"{f(r['first_trade_net_pct'])} | {r['all_n']} | {f(r['all_expectancy_pct'])} |")
        L.append("\n> ถ้าผลดีเฉพาะ lag 0–5 แต่หายที่ 10–15 = สัญญาณพึ่งการเห็น OI เร็วกว่าที่อาจทำได้จริง ต้องวัด latency จริงด้วย `python run.py oi-latency`\n")
    L.append("## คำตอบ 4 ข้อ\n")
    L.append("| คำถาม | คำตอบ |\n|---|---|")
    L.append(f"| Q1 สัญญาณแรกเกิดเมื่อไร | {a.get('Q1_first_signal') or '**ไม่มีสัญญาณก่อน breakout**'} |")
    q2 = a.get("Q2_lead_min")
    L.append(f"| Q2 นำหน้า breakout กี่นาที | {f'{q2:.0f} นาที' if q2 is not None else '-'} |")
    L.append(f"| Q3 false signal ก่อนหน้า | {a.get('Q3_false_before')} ครั้ง ใน {a.get('Q3_days_observed')} วัน |")
    t = a.get("Q4_trade_on_first_signal")
    if t:
        L.append(f"| Q4 เข้าเทรดตามสัญญาณแรก | เข้า {t['entry']:.6g} @ {t['entry_time']} · ออก ({t['reason']}) "
                 f"{t['exit']:.6g} @ {t['exit_time']} · **สุทธิ {t['net_pct']:+.2f}%** (MFE {t['mfe_pct']:+.1f}%, MAE {t['mae_pct']:+.1f}%) |")
    else:
        L.append("| Q4 เข้าเทรดตามสัญญาณแรก | - |")
    L.append(f"\nalert ที่เกิด 'หลัง' breakout (ตามหลัง ไม่นับเป็นการทำนาย): {a.get('late_alerts', 0)} ครั้ง\n")
    if a.get("evidence_at_first_signal"):
        L.append("## หลักฐาน ณ สัญญาณแรก\n\n| เงื่อนไข | ผล | ค่า |\n|---|---|---|")
        for e in a["evidence_at_first_signal"]:
            L.append(f"| {e['name']} | {'ผ่าน' if e['pass'] else 'ไม่ผ่าน'} | {e['value']} |")
        L.append("")
    L.append("## ข้อมูลไหนขยับก่อน (Root-cause candidates)\n")
    L.append("| ลำดับ | stream | ติดครั้งแรก (12 ชม.ก่อน breakout) | นำหน้า (นาที) | อัตราติดปกติ/วัน |\n|---|---|---|---|---|")
    for i, r in enumerate(a.get("first_mover", []), 1):
        L.append(f"| {i} | {r['component']} | {r['first_fire']} | {r['lead_min'] if r['lead_min'] is not None else '-'} | "
                 f"{r['baseline_fires_per_day'] if r['baseline_fires_per_day'] is not None else '-'} |")
    L.append("\n> stream ที่ขยับก่อนแต่ 'อัตราติดปกติ/วัน' สูง = เสียงรบกวน ไม่ใช่สัญญาณ — ดูสองคอลัมน์คู่กันเสมอ")
    L.append("> ข้อมูลตลาดบอกได้แค่ 'ใครซื้อก่อน' ไม่ได้บอก 'ทำไม' (ข่าว/listing/ประกาศ) ต้องเทียบ timeline ข่าวแยก\n")
    s = a["all_trades_summary"]
    rb = a.get("random_baseline", {})
    L.append("## ทุก alert ในช่วงข้อมูล (ไม่ใช่แค่ครั้งที่ถูก)\n")
    L.append(f"การจัดประเภท alert: {a.get('classification_counts')}\n")
    if s.get("n"):
        L.append(f"เทรด {s['n']} ไม้ · win {s['win_rate']*100:.0f}% · Expectancy {s['expectancy_pct']:+.2f}%/ไม้ · "
                 f"Profit Factor {s['profit_factor']:.2f} · ทบต้น {s['total_compound_pct']:+.1f}% · Max DD {s['max_dd_pct']:.1f}%\n")
    if rb:
        L.append(f"Baseline สุ่มเข้า {s.get('n')} ไม้ × {rb['sims']} รอบ: เฉลี่ยสุทธิ median {rb['mean_net_pct_p50']:+.2f}% "
                 f"(5–95%: {rb['p05']:+.2f}% … {rb['p95']:+.2f}%)\n")
        if s.get("n"):
            beat = s["mean_net_pct"] > rb["p95"]
            L.append(f"**สัญญาณชนะการสุ่มที่ระดับ 95% หรือไม่: {'ชนะ' if beat else 'ไม่ชนะ'}** "
                     "(เหรียญเดียว n น้อย — ข้อนี้เป็นแค่ sanity check ไม่ใช่การพิสูจน์)\n")
    L.append("## ข้อควรระวังในการอ่านผล\n")
    L.append("- นี่คือเหรียญเดียวที่ **เลือกมาเพราะรู้แล้วว่ามันพุ่ง** — ผลดีที่นี่ไม่พิสูจน์อะไร ต้องผ่าน Phase 2–3 ก่อน")
    L.append(f"- OI ถือว่าเห็นได้หลัง create_time + {cfg.data.oi_lag_min} นาที (สมมติ) ถ้าลด lag แล้วผลดีขึ้นมาก = ผลพึ่งสมมติฐานนี้")
    L.append(f"- ต้นทุน: fee {cfg.trade.fee_per_side*100:.2f}% + slippage {cfg.trade.slip_per_side*100:.2f}% ต่อข้าง")
    L.append("- เกณฑ์ทั้งหมดอยู่ใน config.py และตั้งก่อนรัน — ถ้าแก้หลังเห็นผล ผลนี้กลายเป็น in-sample")
    return "\n".join(L)
