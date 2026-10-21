#!/usr/bin/env python3
"""
webapp/app.py — แดชบอร์ด TikTok Lab (แนว vidIQ แต่ของเราเอง)

    pip install -r ../requirements.txt
    python webapp/app.py            # ถ้าไม่ได้ตั้ง TIKTOK_CLIENT_KEY จะเข้าโหมด demo อัตโนมัติ
    python webapp/app.py --demo     # บังคับโหมด demo
    เปิด http://localhost:5001

แท็บ: ช่องของฉัน / คู่แข่ง + Outliers / Hashtag / AI ช่วยเขียน
ผล Research API แคชในหน่วยความจำ 1 ชม. — โควตาวันละ 1,000 request หมดเร็วถ้าไม่แคช
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session

LAB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB_DIR))
import analytics as A     # noqa: E402
import tiktok_api as T    # noqa: E402
from demo import DemoSource  # noqa: E402

COMPETITORS_FILE = LAB_DIR / "competitors.json"
CACHE_TTL = 3600
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{2,24}$")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(16))

if "--demo" in sys.argv or not os.environ.get("TIKTOK_CLIENT_KEY"):
    SRC = DemoSource()
else:
    SRC = T.LiveSource()

_CACHE: dict[str, tuple[float, object]] = {}


def cached(key: str, fn):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    val = fn()
    _CACHE[key] = (time.time(), val)
    return val


def _competitors() -> list[str]:
    if COMPETITORS_FILE.exists():
        return json.loads(COMPETITORS_FILE.read_text(encoding="utf-8"))
    return ["rival_food_th", "bkk_eats", "homecook_daily"] if SRC.demo else []


def _save_competitors(names: list[str]) -> None:
    COMPETITORS_FILE.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")


def _clean_username(u: str) -> str:
    u = (u or "").strip().lstrip("@")
    m = re.search(r"tiktok\.com/@([A-Za-z0-9._]+)", u)
    if m:
        u = m.group(1)
    if not USERNAME_RE.match(u):
        raise ValueError("ชื่อบัญชีไม่ถูกต้อง (a-z, 0-9, . และ _ ยาว 2-24 ตัว)")
    return u.lower()


def _my_report() -> dict:
    return cached("me", lambda: A.full_report(SRC.my_account(), SRC.my_videos()))


def _competitor_report(u: str, days: int) -> dict:
    def build():
        acc, vids = SRC.competitor(u, days=days)
        return A.full_report(acc, vids)
    return cached(f"comp:{u}:{days}", build)


@app.errorhandler(T.TikTokError)
def _tt_error(e):
    return jsonify({"error": str(e)}), 502


@app.errorhandler(ValueError)
def _value_error(e):
    return jsonify({"error": str(e)}), 400


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify({"demo": SRC.demo, "connected": SRC.connected(),
                    "research": bool(SRC.research_enabled),
                    "ai": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                               or os.environ.get("ANTHROPIC_PROFILE"))})


# ---------- OAuth ----------
@app.route("/auth/login")
def auth_login():
    url, state = SRC.auth_url()
    session["oauth_state"] = state
    return redirect(url)


@app.route("/auth/callback")
def auth_callback():
    if request.args.get("error"):
        return f"TikTok ปฏิเสธการเชื่อม: {request.args.get('error_description', request.args['error'])}", 400
    if request.args.get("state") != session.pop("oauth_state", None):
        return "state ไม่ตรง — ลองกดเชื่อมบัญชีใหม่", 400
    SRC.exchange_code(request.args.get("code", ""))
    _CACHE.pop("me", None)
    return redirect("/")


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    SRC.logout()
    _CACHE.clear()
    return jsonify({"ok": True})


# ---------- ช่องของฉัน ----------
@app.route("/api/me")
def api_me():
    return jsonify(_my_report())


# ---------- คู่แข่ง + Outliers ----------
@app.route("/api/competitors", methods=["GET", "POST", "DELETE"])
def api_competitors():
    names = _competitors()
    if request.method == "POST":
        u = _clean_username((request.get_json(force=True) or {}).get("username"))
        if u not in names:
            names.append(u)
            _save_competitors(names)
    elif request.method == "DELETE":
        u = _clean_username(request.args.get("username"))
        names = [n for n in names if n != u]
        _save_competitors(names)
    return jsonify(names)


@app.route("/api/competitor/<username>")
def api_competitor(username: str):
    days = min(int(request.args.get("days", 90)), 180)
    return jsonify(_competitor_report(_clean_username(username), days))


@app.route("/api/outliers")
def api_outliers():
    """รวม outlier ของคู่แข่งทุกบัญชี — แต่ละคลิปเทียบกับ median ของบัญชีตัวเอง"""
    days = min(int(request.args.get("days", 90)), 180)
    min_x = float(request.args.get("min_x", A.OUTLIER_X))
    rows, errors = [], {}
    for u in _competitors():
        try:
            rep = _competitor_report(u, days)
        except T.TikTokError as e:
            errors[u] = str(e)
            continue
        rows += A.outliers(rep["videos"], min_x=min_x, top=1000)
    rows.sort(key=lambda v: v["outlier"], reverse=True)
    return jsonify({"outliers": rows[:60], "errors": errors,
                    "hashtags": A.hashtag_stats(rows, min_n=2, top=20)})


# ---------- Hashtag ----------
@app.route("/api/hashtags")
def api_hashtags():
    tags = [t.strip().lstrip("#").lower() for t in request.args.get("tags", "").split(",") if t.strip()]
    if not tags:
        raise ValueError("ใส่ hashtag อย่างน้อย 1 ตัว")
    days = min(int(request.args.get("days", 30)), 90)
    vids = cached(f"tag:{','.join(sorted(tags))}:{days}", lambda: SRC.hashtag_videos(tags, days=days))
    # hashtag_videos มาจากหลายบัญชี → คำนวณ outlier ต่อบัญชี ไม่ใช่รวมกัน
    by_user: dict[str, list[dict]] = {}
    for v in vids:
        by_user.setdefault(v["username"], []).append(v)
    enriched = [x for vs in by_user.values() for x in A.enrich(vs)]
    return jsonify({"n_videos": len(enriched), "n_creators": len(by_user),
                    "median_views": A._med(v["views"] for v in enriched),
                    "related": A.related_tags(enriched, tags),
                    "top_videos": sorted(enriched, key=lambda v: v["views"], reverse=True)[:20],
                    "best_times": A.best_times(enriched)})


# ---------- AI ----------
@app.route("/api/ai", methods=["POST"])
def api_ai():
    import ai_writer as W
    p = request.get_json(force=True) or {}
    topic = (p.get("topic") or "").strip()
    if not topic:
        raise ValueError("ใส่หัวข้อคลิปก่อน")
    report = None
    if p.get("use_my_data"):
        try:
            report = _my_report()
        except T.TikTokError:
            report = None
    try:
        out = W.generate(topic, niche=p.get("niche", ""), tone=p.get("tone", "เป็นกันเอง"),
                         length_sec=int(p.get("length_sec", 30)), report=report)
    except W.AIError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(out)


if __name__ == "__main__":
    print(f"TikTok Lab — โหมด {'DEMO (ข้อมูลจำลอง)' if SRC.demo else 'LIVE'} → http://localhost:5001")
    app.run(host="127.0.0.1", port=5001, debug=False)
