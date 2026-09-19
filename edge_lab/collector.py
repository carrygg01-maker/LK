#!/usr/bin/env python3
"""
collector.py — เก็บข้อมูลที่ Binance ให้ย้อนหลังแค่ 30 วัน

นี่คือส่วนที่ทำให้คุณมี dataset ที่คนอื่นไม่มี ไม่ใช่เพราะมันยาก
แต่เพราะมันต้องเริ่มเก็บตั้งแต่วันนี้ ย้อนหลังซื้อไม่ได้

รันทุก 5 นาที:
    */5 * * * * cd /path/to/edge_lab && /usr/bin/python3 collector.py >> collect.log 2>&1

เก็บอะไร:
  * open interest          (ย้อนหลังได้แค่ 30 วัน)
  * top trader long/short  (ย้อนหลังได้แค่ 30 วัน)
  * taker buy/sell ratio   (ย้อนหลังได้แค่ 30 วัน)
  * mark/index/funding ปัจจุบัน
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import data as D  # noqa: E402

STORE = Path(__file__).parent / "collected"
STORE.mkdir(exist_ok=True)
TOP_N = 60


def snapshot() -> pd.DataFrame:
    uni = D.perp_symbols()
    uni = uni[~uni["symbol"].str.contains("USDC", regex=True)]
    syms = uni["symbol"].head(TOP_N).tolist()

    prem = {d["symbol"]: d for d in D._get(D.FAPI, "/fapi/v1/premiumIndex")}
    tick = {d["symbol"]: d for d in D._get(D.FAPI, "/fapi/v1/ticker/24hr")}
    now = datetime.now(timezone.utc)

    rows = []
    for s in syms:
        rec = {"ts": now, "symbol": s}
        p, t = prem.get(s, {}), tick.get(s, {})
        rec["mark"] = float(p.get("markPrice", "nan"))
        rec["index"] = float(p.get("indexPrice", "nan"))
        rec["funding_next"] = float(p.get("lastFundingRate", "nan"))
        rec["basis"] = rec["mark"] / rec["index"] - 1 if rec["index"] else float("nan")
        rec["qvol_24h"] = float(t.get("quoteVolume", "nan"))
        try:
            oi = D._get(D.FAPI, "/fapi/v1/openInterest", {"symbol": s})
            rec["oi"] = float(oi["openInterest"])
        except Exception:
            rec["oi"] = float("nan")
        try:
            ls = D._get(D.FAPI, "/futures/data/topLongShortPositionRatio",
                        {"symbol": s, "period": "5m", "limit": 1})
            rec["top_ls_ratio"] = float(ls[0]["longShortRatio"]) if ls else float("nan")
        except Exception:
            rec["top_ls_ratio"] = float("nan")
        try:
            tv = D._get(D.FAPI, "/futures/data/takerlongshortRatio",
                        {"symbol": s, "period": "5m", "limit": 1})
            rec["taker_buy_sell"] = float(tv[0]["buySellRatio"]) if tv else float("nan")
        except Exception:
            rec["taker_buy_sell"] = float("nan")
        rows.append(rec)
    return pd.DataFrame(rows)


def main():
    df = snapshot()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    f = STORE / f"snap_{day}.parquet"
    if f.exists():
        df = pd.concat([pd.read_parquet(f), df], ignore_index=True)
    df.to_parquet(f, index=False)
    print(f"{datetime.now(timezone.utc):%F %T}  เก็บ {len(df)} แถวสะสม -> {f.name}")


if __name__ == "__main__":
    main()
