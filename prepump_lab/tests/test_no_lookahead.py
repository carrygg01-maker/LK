"""
test_no_lookahead.py — ถ้าไฟล์นี้ไม่ผ่าน ผล backtest ทุกตัวเชื่อไม่ได้

1. ค่า feature/สัญญาณ ณ เวลา T ที่คำนวณจาก "ข้อมูลทั้งหมด" ต้องเท่ากับ
   ค่าที่คำนวณจาก "ข้อมูลที่เผยแพร่แล้ว ณ T" เท่านั้น (ทุก T ที่สุ่ม)
2. features.py / signals.py ห้าม import labels.py
3. random walk ล้วน (ไม่มี edge) ต้องไม่ผ่านเกณฑ์
4. ข้อมูลที่ฝัง lead ไว้ ต้องวัด lead ได้ใกล้ค่าที่ฝัง
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import features  # noqa: E402
import forensic  # noqa: E402
import signals  # noqa: E402
import synthetic  # noqa: E402
from config import DEFAULT  # noqa: E402

COLS = ["oi", "oi_chg", "fut_cvd_z", "spot_cvd_z", "rvol", "ret_ext", "funding"]


def test_truncation_equivalence():
    raw, _ = synthetic.make(days=4, seed=3)
    full = features.build(raw, DEFAULT)
    comp_full = signals.components(full, DEFAULT)
    rng = np.random.default_rng(0)
    for t in rng.choice(full.index[1500:-5], 25, replace=False):
        t = pd.Timestamp(t)
        decide = t + pd.Timedelta(minutes=1)
        vis = features.visible_raw(raw, decide, DEFAULT)
        part = features.build(vis, DEFAULT)
        assert part.index[-1] == t, (part.index[-1], t)
        a = full.loc[t, COLS].astype(float).values
        b = part.loc[t, COLS].astype(float).values
        assert np.allclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-12), (t, dict(zip(COLS, a)), dict(zip(COLS, b)))
        cp = signals.components(part, DEFAULT)
        assert (cp.loc[t] == comp_full.loc[t]).all(), t


def test_no_label_import():
    for f in ["features.py", "signals.py"]:
        tree = ast.parse((HERE / f).read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                 for a in n.names} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert "labels" not in names, f"{f} import labels"


def test_planted_lead_measured():
    raw, truth = synthetic.make(days=14, seed=1, build_min=90)
    day = truth["real"][0]["pump_start"][:10]
    res = forensic.run(raw, DEFAULT, symbol="SYN", focus_start=day, focus_end=day)
    a = res["answers"]
    assert a["main_episode"] is not None
    assert a["Q2_lead_min"] is not None, "ควรจับ pump ที่ฝัง build-up ไว้ได้"
    # breakout (+5%) เกิดหลังเริ่มพุ่ง ~ 40 นาที -> lead ต้องอยู่ในช่วง build_min ± 60
    assert 60 <= a["Q2_lead_min"] <= 200, a["Q2_lead_min"]
    assert a["Q3_false_before"] >= 1, "decoy ต้องถูกนับเป็น false signal"


def test_null_has_no_alpha():
    raw, _ = synthetic.make(days=14, seed=9, edge=False)
    res = forensic.run(raw, DEFAULT, symbol="NULL")
    s = res["answers"]["all_trades_summary"]
    assert s.get("n", 0) == 0 or s["mean_net_pct"] < 0.5, s




def test_quality_detects_gaps_and_missing_oi():
    import quality
    raw, _ = synthetic.make(days=4, seed=2)
    fk = raw["fut_kl"]
    raw["fut_kl"] = fk.drop(fk.index[2000:2300])           # ขาด 300 นาที
    raw["metrics"] = raw["metrics"].iloc[: len(raw["metrics"]) // 2]   # OI หายครึ่งหลัง
    q = quality.symbol_quality("X", raw)
    assert q["status"] != "ready", q
    assert any(g["missing_min"] >= 299 for g in q["datasets"]["fut_kl"]["gaps"]), q["datasets"]["fut_kl"]["gaps"]
    assert q["datasets"]["metrics"]["coverage"] < 0.6


def test_no_signal_blamed_on_data_when_oi_missing():
    raw, truth = synthetic.make(days=14, seed=1)
    day = truth["real"][0]["pump_start"][:10]
    t = pd.Timestamp(truth["real"][0]["pump_start"])
    m = raw["metrics"]
    raw["metrics"] = m[(m.index < t - pd.Timedelta(hours=14)) | (m.index > t + pd.Timedelta(hours=2))]
    a = forensic.run(raw, DEFAULT, day, day, symbol="X")["answers"]
    assert a["Q1_first_signal"] is None
    oi = [r for r in a["near_miss"] if r["metric"].startswith("OI")][0]
    assert oi["cause"] == "data", oi


def test_metrics_math():
    import sim
    tr = pd.DataFrame({"net_pct": [10.0, -5.0, -5.0, 20.0]})
    s = sim.summarize(tr)
    assert abs(s["expectancy_pct"] - 5.0) < 1e-9
    assert abs(s["profit_factor"] - 3.0) < 1e-9      # (10+20)/(5+5)
    assert abs(s["breakeven_win_rate"] - 5 / (15 + 5)) < 1e-9



def test_gap_right_before_pump_is_unknown_not_false():
    """95% ครบทั้งช่วง แต่ OI หาย 10 นาทีก่อนสัญญาณพอดี -> ต้องเป็น UNKNOWN/INCONCLUSIVE ไม่ใช่ FAIL"""
    raw, truth = synthetic.make(days=14, seed=1)
    day = truth["real"][0]["pump_start"][:10]
    t = pd.Timestamp(truth["real"][0]["pump_start"])
    m = raw["metrics"]
    raw["metrics"] = m[(m.index < t - pd.Timedelta(minutes=55)) | (m.index > t - pd.Timedelta(minutes=40))]
    res = forensic.run(raw, DEFAULT, day, day, symbol="X")
    a = res["answers"]
    assert a["lead_window_unknown_min"] > 0
    a["lag_sensitivity"] = forensic.lag_sensitivity(raw, DEFAULT, day, day)
    v = forensic.verdict(a, DEFAULT)
    assert v["verdict"] in ("INCONCLUSIVE", "PASS"), v   # ห้ามเป็น FAIL เพราะข้อมูลขาด
    if a["Q1_first_signal"] is None:
        assert v["verdict"] == "INCONCLUSIVE", v


def test_unknown_state():
    raw, _ = synthetic.make(days=4, seed=3)
    m = raw["metrics"]
    raw["metrics"] = m.drop(m.index[500:503])            # หาย 15 นาที
    b = features.build(raw, DEFAULT)
    u = signals.unknown(b)
    gap_t = m.index[502]
    assert u.loc[gap_t:gap_t + pd.Timedelta(minutes=30), "oi_expansion"].any()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✓ {name}")
