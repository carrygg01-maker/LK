"""
demo.py — แหล่งข้อมูลจำลอง หน้าตาเหมือน LiveSource ทุกอย่าง

ใช้ตอนยังไม่มี developer app หรือแค่อยากลองหน้าเว็บ ข้อมูลสุ่มแบบ deterministic
(seed จากชื่อบัญชี) และฝังรูปแบบไว้ให้ analytics เจอ:
  - ยอดวิวกระจายแบบ lognormal + มี outlier ประปราย (เหมือนของจริง)
  - โพสต์ช่วง 19-22 น. (เวลาไทย) ได้วิวดีกว่า
  - บาง hashtag ดันวิวขึ้น บางตัวไม่มีผล
"""
from __future__ import annotations

import hashlib
import random
import time

NICHE_TAGS = ["อาหาร", "รีวิวอาหาร", "สูตรอาหาร", "ทำอาหาร", "streetfood", "ของอร่อย",
              "กินอะไรดี", "foodtiktok", "คาเฟ่", "ขนม", "มื้อเย็น", "ง่ายๆ"]
BOOST = {"สูตรอาหาร": 1.8, "ง่ายๆ": 1.5, "streetfood": 1.4, "กินอะไรดี": 1.3}
GENERIC = ["fyp", "foryou", "tiktokthailand", "viral"]
TOPICS = ["ข้าวผัดกระเพรา 5 นาที", "รีวิวร้านลับย่านบางรัก", "ขนมไทยโบราณ", "เมนูไข่ 3 แบบ",
          "ก๋วยเตี๋ยวเรือเจ้าดัง", "คาเฟ่เปิดใหม่", "ต้มยำน้ำข้น", "เมนูประหยัดงบ 50 บาท",
          "หมูกระทะที่บ้าน", "ส้มตำปูปลาร้า", "ชาไทยทำเอง", "ข้าวมันไก่สูตรเด็ด"]


def _rng(key: str) -> random.Random:
    return random.Random(int(hashlib.md5(key.encode()).hexdigest()[:8], 16))


def _videos(username: str, n: int, base_views: float, days: int = 120) -> list[dict]:
    rnd = _rng(username)
    now = int(time.time())
    out = []
    for i in range(n):
        ts = now - rnd.randint(0, days * 86400)
        hour_th = (ts // 3600 + 7) % 24
        tags = rnd.sample(NICHE_TAGS, rnd.randint(2, 4)) + rnd.sample(GENERIC, rnd.randint(0, 2))
        mult = rnd.lognormvariate(0, 0.8)
        mult *= 1.7 if 19 <= hour_th <= 22 else (0.7 if 1 <= hour_th <= 7 else 1.0)
        for t in tags:
            mult *= BOOST.get(t, 1.0)
        if rnd.random() < 0.05:
            mult *= rnd.uniform(5, 15)
        views = int(base_views * mult)
        like_rate = rnd.uniform(0.04, 0.12)
        topic = rnd.choice(TOPICS)
        out.append({
            "id": f"{username}-{i}",
            "username": username,
            "desc": f"{topic} " + " ".join("#" + t for t in tags),
            "create_time": ts,
            "duration": rnd.randint(9, 90),
            "views": views,
            "likes": int(views * like_rate),
            "comments": int(views * like_rate * rnd.uniform(0.02, 0.08)),
            "shares": int(views * like_rate * rnd.uniform(0.03, 0.2)),
            "hashtags": [t.lower() for t in tags],
            "cover_url": "",
            "share_url": f"https://www.tiktok.com/@{username}/video/{i}",
        })
    return sorted(out, key=lambda v: v["create_time"], reverse=True)


class DemoSource:
    demo = True
    research_enabled = True

    def auth_url(self):
        return "/auth/callback?code=demo&state=demo", "demo"

    def exchange_code(self, code: str) -> None:
        pass

    def connected(self) -> bool:
        return True

    def logout(self) -> None:
        pass

    def my_account(self) -> dict:
        return {"username": "demo_kitchen", "display_name": "ครัวเดโม่", "bio": "สูตรอาหารง่ายๆ ทุกวัน",
                "avatar_url": "", "followers": 48200, "following": 210, "likes_total": 1_250_000,
                "video_count": 140}

    def my_videos(self, limit: int = 300) -> list[dict]:
        return _videos("demo_kitchen", 140, 12000)[:limit]

    def competitor(self, username: str, days: int = 90, limit: int = 300):
        rnd = _rng("acc-" + username)
        base = rnd.choice([5000, 20000, 60000, 150000])
        acc = {"username": username, "display_name": username.replace("_", " ").title(), "bio": "",
               "avatar_url": "", "followers": int(base * rnd.uniform(3, 12)), "following": rnd.randint(50, 900),
               "likes_total": int(base * rnd.uniform(40, 200)), "video_count": rnd.randint(60, 400)}
        return acc, _videos(username, rnd.randint(40, 120), base, days)[:limit]

    def hashtag_videos(self, tags: list[str], days: int = 30, limit: int = 500) -> list[dict]:
        pool = []
        for i in range(8):
            pool += _videos(f"creator{i}_{'_'.join(tags)}", 60, 20000, days)
        wanted = {t.lower() for t in tags}
        hits = [v for v in pool if wanted & set(v["hashtags"])]
        return hits[:limit]
