"""
stats.py — เครื่องมือประเมินที่ออกแบบมาเพื่อ "ไม่หลอกตัวเอง"

หลักสามข้อ:
1. หักต้นทุนก่อนเสมอ  ไม่มี metric ไหนรายงานแบบ gross
2. IS / OOS แยกกัน     ตัวเลข OOS เท่านั้นที่นับ
3. CI จาก block bootstrap  เพราะ return มัน autocorrelate, t-test ธรรมดาโกหก
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

# ----------------------------------------------------------------- ค่าต้นทุน
TAKER_FEE = 0.00045      # 0.045% ต่อข้าง (ไม่ลด BNB)
SLIPPAGE = 0.00010       # 1 bp ต่อข้าง สำหรับเหรียญ liquid; เหรียญบางให้เพิ่ม
COST_PER_SIDE = TAKER_FEE + SLIPPAGE
COST_ROUND_TRIP = 2 * COST_PER_SIDE      # ~0.11% = 11 bps

MIN_EVENTS = 100         # ต่ำกว่านี้ไม่รายงานว่า "ผ่าน" เด็ดขาด


@dataclass
class Result:
    name: str
    n: int = 0
    mean_bps: float = 0.0            # net หลังหักต้นทุนแล้ว
    median_bps: float = 0.0
    hit_rate: float = 0.0
    tstat: float = 0.0
    sharpe: float = 0.0              # annualized
    ci_low_bps: float = 0.0          # bootstrap 95%
    ci_high_bps: float = 0.0
    max_dd: float = 0.0
    turnover_cost_bps: float = 0.0
    note: str = ""
    passed: bool = False

    def row(self) -> dict:
        return asdict(self)


def block_bootstrap_ci(x: np.ndarray, block: int | None = None,
                       n_boot: int = 2000, alpha: float = 0.05,
                       seed: int = 7) -> tuple[float, float]:
    """
    Moving-block bootstrap ของค่าเฉลี่ย
    เก็บ autocorrelation ไว้ ทำให้ CI กว้างตามความจริง ไม่แคบแบบหลอกๆ
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 20:
        return (np.nan, np.nan)
    if block is None:
        block = max(1, int(round(n ** (1 / 3))))
    n_blocks = int(np.ceil(n / block))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, max(1, n - block + 1), size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]
    means = x[np.clip(idx, 0, n - 1)].mean(axis=1)
    return (float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2)))


def max_drawdown(returns: np.ndarray) -> float:
    eq = np.cumprod(1 + np.asarray(returns, dtype=float))
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1).min())


def evaluate(returns: pd.Series, name: str, bars_per_year: float,
             cost: float = COST_ROUND_TRIP, note: str = "") -> Result:
    """
    returns: gross return ต่อ 1 ครั้งที่เข้าเทรด (ไม่ใช่ต่อแท่ง)
    cost:    ต้นทุนไป-กลับ หักออกจากทุกไม้
    """
    r = pd.Series(returns).dropna().astype(float)
    net = r - cost
    res = Result(name=name, n=len(net), note=note,
                 turnover_cost_bps=round(cost * 1e4, 2))
    if len(net) < 20:
        res.note = (note + " | ข้อมูลน้อยเกินไป").strip(" |")
        return res

    v = net.values
    res.mean_bps = float(v.mean() * 1e4)
    res.median_bps = float(np.median(v) * 1e4)
    res.hit_rate = float((v > 0).mean())
    sd = v.std(ddof=1)
    res.tstat = float(v.mean() / (sd / np.sqrt(len(v)))) if sd > 0 else 0.0
    res.sharpe = float(v.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0
    lo, hi = block_bootstrap_ci(v)
    res.ci_low_bps, res.ci_high_bps = lo * 1e4, hi * 1e4
    res.max_dd = max_drawdown(v)

    # เกณฑ์ผ่าน: ต้องครบทุกข้อ
    res.passed = bool(
        res.n >= MIN_EVENTS
        and res.ci_low_bps > 0          # CI ล่างต้องเหนือศูนย์ (หลังหักต้นทุนแล้ว)
        and abs(res.tstat) > 3.0        # เข้มกว่า 1.96 เพราะเราทดสอบหลายข้อ
        and res.sharpe > 0.5
    )
    return res


def split_is_oos(df: pd.DataFrame, frac: float = 0.70) -> tuple[pd.DataFrame, pd.DataFrame]:
    """แบ่งตามเวลา ไม่สุ่ม — OOS คือช่วงท้ายที่ห้ามแตะจนกว่าจะสรุป"""
    df = df.sort_index()
    cut = int(len(df) * frac)
    return df.iloc[:cut], df.iloc[cut:]


def deflated_threshold(n_trials: int, alpha: float = 0.05) -> float:
    """
    Bonferroni: ทดสอบ n ข้อ ต้องใช้เกณฑ์ t ที่เข้มขึ้น
    ทดสอบ 6 ข้อที่ alpha=0.05 -> ต้องการ |t| ประมาณ 2.6+
    """
    from scipy import stats as sps
    return float(sps.norm.ppf(1 - alpha / (2 * n_trials)))


def table(results: list[Result]) -> pd.DataFrame:
    df = pd.DataFrame([r.row() for r in results])
    if df.empty:
        return df
    cols = ["name", "n", "mean_bps", "ci_low_bps", "ci_high_bps",
            "tstat", "sharpe", "hit_rate", "max_dd", "passed", "note"]
    df = df[cols]
    for c in ["mean_bps", "ci_low_bps", "ci_high_bps", "tstat", "sharpe", "max_dd"]:
        df[c] = df[c].round(2)
    df["hit_rate"] = (df["hit_rate"] * 100).round(1)
    return df
