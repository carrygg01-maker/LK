"""
analytics.py — ตัวเลขที่ใช้ตัดสินใจ ไม่ใช่ตัวเลขสวยๆ

หลักคิด:
  - ใช้ median ไม่ใช้ mean — ยอดวิว TikTok หางยาวมาก คลิปไวรัลคลิปเดียวทำค่าเฉลี่ยพัง
  - Outlier score = วิวคลิปนั้น ÷ median วิวของบัญชีเดียวกัน (แบบ vidIQ)
    ≥ 3x แปลว่าคลิปนี้ "ทำงาน" เกินมาตรฐานของเจ้าของเอง — นี่คือสิ่งที่ควรศึกษา
  - ทุกกลุ่มมี n กำกับ และกรองกลุ่มที่ n น้อยเกินไปทิ้ง
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("TIKTOK_TZ", "Asia/Bangkok"))
OUTLIER_X = 3.0
DAYS_TH = ["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]
GENERIC_TAGS = {"fyp", "foryou", "foryoupage", "viral", "tiktok", "xyzbca", "fypシ"}


def _med(xs) -> float:
    xs = list(xs)
    return float(median(xs)) if xs else 0.0


def enrich(videos: list[dict]) -> list[dict]:
    base = _med(v["views"] for v in videos) or 1.0
    out = []
    for v in videos:
        eng = v["likes"] + v["comments"] + v["shares"]
        dt = datetime.fromtimestamp(v["create_time"], TZ)
        out.append({**v,
                    "er": round(eng / v["views"] * 100, 2) if v["views"] else 0.0,
                    "share_rate": round(v["shares"] / v["views"] * 100, 3) if v["views"] else 0.0,
                    "outlier": round(v["views"] / base, 2),
                    "posted": dt.strftime("%Y-%m-%d %H:%M"),
                    "weekday": dt.weekday(), "hour": dt.hour})
    return out


def summary(account: dict, videos: list[dict]) -> dict:
    now = datetime.now(timezone.utc).timestamp()
    last30 = [v for v in videos if now - v["create_time"] <= 30 * 86400]
    prev30 = [v for v in videos if 30 * 86400 < now - v["create_time"] <= 60 * 86400]
    m30, mp = _med(v["views"] for v in last30), _med(v["views"] for v in prev30)
    return {
        "account": account,
        "n_videos": len(videos),
        "median_views": _med(v["views"] for v in videos),
        "median_er": _med(v["er"] for v in videos),
        "posts_last30": len(last30),
        "median_views_last30": m30,
        "median_views_prev30": mp,
        "trend_pct": round((m30 / mp - 1) * 100, 1) if mp else None,
        "outlier_count": sum(v["outlier"] >= OUTLIER_X for v in videos),
    }


def outliers(videos: list[dict], min_x: float = OUTLIER_X, top: int = 30) -> list[dict]:
    hits = [v for v in videos if v["outlier"] >= min_x]
    return sorted(hits, key=lambda v: v["outlier"], reverse=True)[:top]


def best_times(videos: list[dict], min_n: int = 3) -> dict:
    """median วิวแยกตามวัน×ช่วงเวลา (ช่วงละ 3 ชม.) — ช่องที่ n < min_n เป็น None"""
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    by_hour: dict[int, list[int]] = defaultdict(list)
    for v in videos:
        grid[(v["weekday"], v["hour"] // 3)].append(v["views"])
        by_hour[v["hour"]].append(v["views"])
    cells = [[({"median": _med(grid[(d, b)]), "n": len(grid[(d, b)])}
               if len(grid[(d, b)]) >= min_n else {"median": None, "n": len(grid[(d, b)])})
              for b in range(8)] for d in range(7)]
    hours = [{"hour": h, "median": _med(by_hour[h]), "n": len(by_hour[h])}
             for h in range(24) if len(by_hour[h]) >= min_n]
    best = sorted(hours, key=lambda x: x["median"], reverse=True)[:3]
    return {"days": DAYS_TH, "blocks": [f"{b*3:02d}-{b*3+3:02d}" for b in range(8)],
            "cells": cells, "best_hours": best}


def hashtag_stats(videos: list[dict], min_n: int = 3, top: int = 40) -> list[dict]:
    """lift = median วิวของคลิปที่ใช้แท็กนี้ ÷ median วิวทั้งหมด (>1 = ดีกว่าค่ากลาง)"""
    base = _med(v["views"] for v in videos) or 1.0
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for v in videos:
        for t in set(v["hashtags"]):
            by_tag[t].append(v)
    rows = []
    for t, vs in by_tag.items():
        if len(vs) < min_n:
            continue
        mv = _med(x["views"] for x in vs)
        rows.append({"tag": t, "n": len(vs), "median_views": mv,
                     "median_er": _med(x["er"] for x in vs), "lift": round(mv / base, 2),
                     "generic": t in GENERIC_TAGS})
    return sorted(rows, key=lambda r: (r["lift"], r["n"]), reverse=True)[:top]


def related_tags(videos: list[dict], seed: list[str], top: int = 25) -> list[dict]:
    seed_set = {s.lower().lstrip("#") for s in seed}
    rows = [r for r in hashtag_stats(videos, min_n=2, top=500) if r["tag"] not in seed_set]
    return sorted(rows, key=lambda r: r["n"] * r["lift"], reverse=True)[:top]


def weekly_trend(videos: list[dict], weeks: int = 12) -> list[dict]:
    now = datetime.now(TZ)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for w in range(weeks - 1, -1, -1):
        a = (start - timedelta(weeks=w)).timestamp()
        b = a + 7 * 86400
        vs = [v["views"] for v in videos if a <= v["create_time"] < b]
        out.append({"week": datetime.fromtimestamp(a, TZ).strftime("%d/%m"),
                    "posts": len(vs), "median_views": _med(vs)})
    return out


def duration_buckets(videos: list[dict], min_n: int = 3) -> list[dict]:
    edges = [(0, 15), (15, 30), (30, 60), (60, 180), (180, 10_000)]
    out = []
    for lo, hi in edges:
        vs = [v for v in videos if lo <= v["duration"] < hi]
        if len(vs) >= min_n:
            out.append({"range": f"{lo}-{hi if hi < 10_000 else '∞'} วิ", "n": len(vs),
                        "median_views": _med(v["views"] for v in vs),
                        "median_er": _med(v["er"] for v in vs)})
    return out


def full_report(account: dict, raw_videos: list[dict]) -> dict:
    vids = enrich(raw_videos)
    return {"summary": summary(account, vids),
            "videos": sorted(vids, key=lambda v: v["create_time"], reverse=True),
            "outliers": outliers(vids),
            "best_times": best_times(vids),
            "hashtags": hashtag_stats(vids),
            "trend": weekly_trend(vids),
            "durations": duration_buckets(vids)}
