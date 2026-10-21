"""
tiktok_api.py — ตัวเชื่อม TikTok API ทางการ (open.tiktokapis.com v2)

ใช้ 2 ชุด API:
  - Login Kit + Display API  → ข้อมูลบัญชีและคลิปของ "เรา" (OAuth ของผู้ใช้)
      scopes: user.info.basic, user.info.profile, user.info.stats, video.list
  - Research API             → ข้อมูลสาธารณะของบัญชีอื่น/hashtag (ต้องได้รับอนุมัติจาก TikTok)
      ใช้ client access token (grant_type=client_credentials) ของแอปเดียวกัน

ทุกฟังก์ชันคืนข้อมูลในรูปแบบกลางเดียวกัน (ดู _norm_video / _norm_account)
เพื่อให้ analytics.py ไม่ต้องรู้ว่าข้อมูลมาจาก Display, Research หรือ demo
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

import requests

API = "https://open.tiktokapis.com"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,user.info.profile,user.info.stats,video.list"

LAB_DIR = Path(__file__).resolve().parent
TOKEN_FILE = LAB_DIR / ".tokens.json"

DISPLAY_VIDEO_FIELDS = ("id,title,video_description,create_time,cover_image_url,share_url,"
                        "duration,view_count,like_count,comment_count,share_count")
DISPLAY_USER_FIELDS = ("open_id,avatar_url,display_name,username,bio_description,"
                       "follower_count,following_count,likes_count,video_count")
RESEARCH_VIDEO_FIELDS = ("id,video_description,create_time,region_code,share_count,view_count,"
                         "like_count,comment_count,hashtag_names,username,music_id,video_duration")
RESEARCH_USER_FIELDS = ("display_name,bio_description,avatar_url,is_verified,follower_count,"
                        "following_count,likes_count,video_count")

HASHTAG_RE = re.compile(r"#([\w฀-๿]+)")


class TikTokError(RuntimeError):
    pass


def _norm_account(d: dict, username: str | None = None) -> dict:
    return {
        "username": d.get("username") or username or "",
        "display_name": d.get("display_name", ""),
        "bio": d.get("bio_description", ""),
        "avatar_url": d.get("avatar_url", ""),
        "followers": int(d.get("follower_count") or 0),
        "following": int(d.get("following_count") or 0),
        "likes_total": int(d.get("likes_count") or 0),
        "video_count": int(d.get("video_count") or 0),
    }


def _norm_video(d: dict, username: str = "") -> dict:
    desc = d.get("video_description") or d.get("title") or ""
    tags = d.get("hashtag_names") or HASHTAG_RE.findall(desc)
    return {
        "id": str(d.get("id", "")),
        "username": d.get("username") or username,
        "desc": desc,
        "create_time": int(d.get("create_time") or 0),
        "duration": int(d.get("duration") or d.get("video_duration") or 0),
        "views": int(d.get("view_count") or 0),
        "likes": int(d.get("like_count") or 0),
        "comments": int(d.get("comment_count") or 0),
        "shares": int(d.get("share_count") or 0),
        "hashtags": [t.lower() for t in tags],
        "cover_url": d.get("cover_image_url", ""),
        "share_url": d.get("share_url", ""),
    }


def _check(resp: requests.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        raise TikTokError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    err = body.get("error") or {}
    if resp.status_code >= 400 or (err.get("code") not in (None, "", "ok")):
        raise TikTokError(f"{err.get('code', resp.status_code)}: {err.get('message', resp.text[:200])}"
                          f" (log_id={err.get('log_id', '-')})")
    return body


class LiveSource:
    """แหล่งข้อมูลจริง — ต้องตั้ง TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REDIRECT_URI"""

    demo = False

    def __init__(self):
        self.client_key = os.environ["TIKTOK_CLIENT_KEY"]
        self.client_secret = os.environ["TIKTOK_CLIENT_SECRET"]
        self.redirect_uri = os.environ.get("TIKTOK_REDIRECT_URI", "http://localhost:5001/auth/callback")
        self.research_enabled = os.environ.get("TIKTOK_RESEARCH", "0") == "1"
        self.http = requests.Session()
        self._client_token: tuple[str, float] | None = None

    # ---------- OAuth ของผู้ใช้ (Login Kit) ----------
    def auth_url(self) -> tuple[str, str]:
        state = secrets.token_urlsafe(16)
        q = {"client_key": self.client_key, "scope": SCOPES, "response_type": "code",
             "redirect_uri": self.redirect_uri, "state": state}
        return AUTH_URL + "?" + urllib.parse.urlencode(q), state

    def _token_request(self, data: dict) -> dict:
        data = {"client_key": self.client_key, "client_secret": self.client_secret, **data}
        r = self.http.post(f"{API}/v2/oauth/token/", data=data, timeout=30,
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
        body = _check(r)
        if "access_token" not in body:
            raise TikTokError(f"{body.get('error', '')}: {body.get('error_description', body)}")
        return body

    def exchange_code(self, code: str) -> None:
        tok = self._token_request({"code": code, "grant_type": "authorization_code",
                                   "redirect_uri": self.redirect_uri})
        self._save_user_token(tok)

    def _save_user_token(self, tok: dict) -> None:
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 0)) - 60
        TOKEN_FILE.write_text(json.dumps(tok), encoding="utf-8")
        os.chmod(TOKEN_FILE, 0o600)

    def connected(self) -> bool:
        return TOKEN_FILE.exists()

    def logout(self) -> None:
        TOKEN_FILE.unlink(missing_ok=True)

    def _user_token(self) -> str:
        if not TOKEN_FILE.exists():
            raise TikTokError("ยังไม่ได้เชื่อมบัญชี TikTok — กด 'เชื่อมบัญชี' ก่อน")
        tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        if time.time() >= tok.get("expires_at", 0):
            tok = self._token_request({"grant_type": "refresh_token",
                                       "refresh_token": tok["refresh_token"]})
            self._save_user_token(tok)
        return tok["access_token"]

    # ---------- Display API: ข้อมูลของเรา ----------
    def my_account(self) -> dict:
        r = self.http.get(f"{API}/v2/user/info/", params={"fields": DISPLAY_USER_FIELDS},
                          headers={"Authorization": f"Bearer {self._user_token()}"}, timeout=30)
        return _norm_account(_check(r)["data"]["user"])

    def my_videos(self, limit: int = 300) -> list[dict]:
        out, cursor = [], None
        headers = {"Authorization": f"Bearer {self._user_token()}"}
        while len(out) < limit:
            body = {"max_count": 20}
            if cursor:
                body["cursor"] = cursor
            r = self.http.post(f"{API}/v2/video/list/", params={"fields": DISPLAY_VIDEO_FIELDS},
                               json=body, headers=headers, timeout=30)
            data = _check(r)["data"]
            out += [_norm_video(v) for v in data.get("videos", [])]
            if not data.get("has_more"):
                break
            cursor = data.get("cursor")
        return out[:limit]

    # ---------- Research API: ข้อมูลสาธารณะ ----------
    def _research_token(self) -> str:
        if not self.research_enabled:
            raise TikTokError("Research API ยังไม่เปิด — แอปต้องได้รับอนุมัติ Research API "
                              "แล้วตั้ง TIKTOK_RESEARCH=1")
        if self._client_token and time.time() < self._client_token[1]:
            return self._client_token[0]
        tok = self._token_request({"grant_type": "client_credentials"})
        self._client_token = (tok["access_token"], time.time() + int(tok.get("expires_in", 7200)) - 60)
        return self._client_token[0]

    def _research_post(self, path: str, fields: str, body: dict) -> dict:
        r = self.http.post(f"{API}{path}", params={"fields": fields}, json=body, timeout=60,
                           headers={"Authorization": f"Bearer {self._research_token()}"})
        return _check(r)["data"]

    def _query_videos(self, condition: dict, days: int, limit: int) -> list[dict]:
        """Research API จำกัดช่วงวันที่ครั้งละ ≤30 วัน — ไล่ย้อนทีละหน้าต่าง"""
        out: list[dict] = []
        end = date.today()
        remaining = days
        while remaining > 0 and len(out) < limit:
            span = min(30, remaining)
            start = end - timedelta(days=span - 1)
            cursor, search_id = 0, None
            while len(out) < limit:
                body = {"query": {"and": [condition]}, "max_count": 100,
                        "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"),
                        "cursor": cursor}
                if search_id:
                    body["search_id"] = search_id
                data = self._research_post("/v2/research/video/query/", RESEARCH_VIDEO_FIELDS, body)
                out += [_norm_video(v) for v in data.get("videos", [])]
                if not data.get("has_more"):
                    break
                cursor, search_id = data.get("cursor", 0), data.get("search_id")
            end = start - timedelta(days=1)
            remaining -= span
        return list({v["id"]: v for v in out}.values())[:limit]

    def competitor(self, username: str, days: int = 90, limit: int = 300) -> tuple[dict, list[dict]]:
        acc = _norm_account(self._research_post("/v2/research/user/info/", RESEARCH_USER_FIELDS,
                                                {"username": username}), username)
        vids = self._query_videos({"operation": "IN", "field_name": "username",
                                   "field_values": [username]}, days, limit)
        return acc, vids

    def hashtag_videos(self, tags: list[str], days: int = 30, limit: int = 500) -> list[dict]:
        return self._query_videos({"operation": "IN", "field_name": "hashtag_name",
                                   "field_values": tags}, days, limit)
