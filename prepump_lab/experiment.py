"""
experiment.py — ล็อกการทดลองไว้ล่วงหน้า (pre-registration)

experiments/<name>.json ระบุก่อนเห็นผล:
  - universe        รายชื่อเหรียญตายตัว (ไม่เปลี่ยนตามเหรียญที่ผู้ใช้เปิดดู)
  - design_symbols  เหรียญที่ใช้ออกแบบสูตร (เช่น ESPORTSUSDT) — ประกาศชัด ไม่ตัดอัตโนมัติ
  - splits          train / validation / test ตามช่วงเวลา
  - code_hash, git_commit, config (ทั้งก้อน), data_hash ต่อเหรียญ
  - lag assumptions พร้อมหมายเหตุว่าเป็นสมมติฐาน

ชุด test เปิดได้ "ครั้งเดียว" ผลครั้งแรกเท่านั้นที่นับเป็นการทดสอบแบบปิดตา
ทุกครั้งที่เปิดถูกบันทึกต่อท้ายใน test_runs (เพิ่มได้อย่างเดียว)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
EXP_DIR = HERE / "experiments"
CODE_FILES = ["config.py", "features.py", "signals.py", "labels.py", "sim.py", "backtest.py", "experiment.py"]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def code_hash() -> str:
    h = hashlib.sha256()
    for f in CODE_FILES:
        h.update(f.encode())
        h.update((HERE / f).read_bytes())
    return h.hexdigest()[:16]


def git_commit() -> dict:
    try:
        c = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=HERE, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--", "."], cwd=HERE, text=True).strip())
        return {"commit": c, "dirty": dirty}
    except Exception:  # noqa: BLE001
        return {"commit": None, "dirty": None}


def data_hash(data_dir: Path, sym: str) -> str | None:
    d = data_dir / sym
    files = sorted(d.glob("*.parquet"))
    if not files:
        return None
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def create(name: str, universe: list[str], design_symbols: list[str], train_start: str, val_start: str,
           test_start: str, test_end: str, cfg_dict: dict, data_dir: Path, note: str = "") -> Path:
    EXP_DIR.mkdir(exist_ok=True)
    path = EXP_DIR / f"{name}.json"
    if path.exists():
        raise SystemExit(f"{path} มีอยู่แล้ว — manifest ห้ามเขียนทับ ตั้งชื่อใหม่ถ้าจะเริ่มการทดลองใหม่")
    uni = sorted(set(universe) - set(design_symbols))
    if not (train_start < val_start < test_start <= test_end):
        raise SystemExit("ช่วงเวลาต้องเรียง train_start < val_start < test_start <= test_end")
    m = {
        "name": name,
        "created_at": str(pd.Timestamp.now(tz="UTC")),
        "note": note,
        "universe": uni,
        "design_symbols": sorted(design_symbols),
        "splits": {"train": [train_start, val_start], "validation": [val_start, test_start],
                   "test": [test_start, test_end]},
        "code_hash": code_hash(),
        "git": git_commit(),
        "config": cfg_dict,
        "data_hash": {s: data_hash(data_dir, s) for s in uni},
        "lag_assumption": {
            "oi_lag_min": cfg_dict["data"]["oi_lag_min"],
            "oi_lag_conservative_min": cfg_dict["data"]["oi_lag_conservative_min"],
            "note": "lag วัดจากปัจจุบัน ใช้กับข้อมูลอดีตเป็นสมมติฐาน ไม่ได้พิสูจน์ว่า Binance เผยแพร่เร็วเท่ากันในอดีต "
                    "ผลต้องผ่านที่ lag อนุรักษนิยมด้วย",
        },
        "pass_rule": "ผ่านทุกเกณฑ์ใน backtest.py ทั้งที่ oi_lag_min และ oi_lag_conservative_min",
        "test_runs": [],
    }
    path.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def drift(m: dict, cfg_dict: dict, data_dir: Path) -> list[str]:
    """สิ่งที่เปลี่ยนไปจากตอนล็อก — ถ้ามี ผล test ไม่นับเป็นการทดสอบตาม manifest"""
    out = []
    if code_hash() != m["code_hash"]:
        out.append("โค้ด (features/signals/labels/sim/backtest/config) เปลี่ยนหลังล็อก")
    if json.dumps(cfg_dict, sort_keys=True) != json.dumps(m["config"], sort_keys=True):
        out.append("config.py เปลี่ยนหลังล็อก")
    changed = [s for s in m["universe"] if data_hash(data_dir, s) != m["data_hash"].get(s)]
    if changed:
        out.append(f"ข้อมูลเปลี่ยน/หาย {len(changed)} เหรียญ: {changed[:5]}{'...' if len(changed) > 5 else ''}")
    return out


def record_test_run(path: Path, result: dict) -> int:
    m = load(path)
    m["test_runs"].append({"at": str(pd.Timestamp.now(tz="UTC")), **result})
    Path(path).write_text(json.dumps(m, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return len(m["test_runs"])
