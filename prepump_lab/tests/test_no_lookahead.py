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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✓ {name}")
