"""
ai_writer.py — ให้ Claude เขียน hook / caption / hashtag / สคริปต์ จากข้อมูลจริงของช่อง

ไม่ได้ให้ AI เดาลอยๆ: ส่ง hashtag ที่ lift สูง และคำอธิบายคลิป outlier ไปเป็นบริบท
เพื่อให้สิ่งที่เขียนออกมาสอดคล้องกับสิ่งที่ "ทำงานแล้ว" ในช่อง/คู่แข่ง

ต้องมี ANTHROPIC_API_KEY (หรือ `ant auth login`)
"""
from __future__ import annotations

import json

import anthropic

MODEL = "claude-opus-5-5"

SYSTEM = """คุณคือนักวางกลยุทธ์คอนเทนต์ TikTok ภาษาไทย เขียนให้คนดูหยุดนิ้วภายใน 1-2 วินาทีแรก
- hook ต้องสั้น พูดได้ใน 3 วินาที เปิดด้วยความขัดแย้ง ตัวเลข ผลลัพธ์ หรือคำถามที่คนอยากรู้คำตอบ
- caption กระชับ มี call-to-action ที่เป็นธรรมชาติ
- hashtag ผสมแท็กเฉพาะกลุ่ม (niche) กับแท็กกว้าง ไม่ใส่ # นำหน้าในผลลัพธ์ ไม่เกิน 8 ตัวต่อชุด
- สคริปต์เขียนเป็นจังหวะภาพ+เสียงพูด สำหรับคลิปตามความยาวที่กำหนด
- ถ้ามีข้อมูลช่องให้ ใช้มันเป็นหลักฐาน และอธิบายสั้นๆ ว่าทำไมเลือกแนวนี้
- ห้ามอ้างตัวเลขหรือข้อเท็จจริงที่ไม่มีในข้อมูล"""

SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": {"type": "array", "items": {"type": "string"}},
        "captions": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "script": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "beats": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"time": {"type": "string"}, "visual": {"type": "string"},
                                   "voiceover": {"type": "string"}},
                    "required": ["time", "visual", "voiceover"],
                    "additionalProperties": False}},
                "cta": {"type": "string"},
            },
            "required": ["title", "beats", "cta"],
            "additionalProperties": False,
        },
        "video_ideas": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["hooks", "captions", "hashtags", "script", "video_ideas", "rationale"],
    "additionalProperties": False,
}


class AIError(RuntimeError):
    pass


def _context_block(report: dict | None) -> str:
    if not report:
        return "(ไม่มีข้อมูลช่อง)"
    tags = [f"#{h['tag']} (lift {h['lift']}x, n={h['n']})"
            for h in report.get("hashtags", []) if not h.get("generic") and h["lift"] > 1][:12]
    outs = [f"- {o['outlier']}x | {o['desc'][:120]}" for o in report.get("outliers", [])[:8]]
    bt = report.get("best_times", {}).get("best_hours", [])
    hours = ", ".join(f"{b['hour']:02d}:00" for b in bt)
    return ("hashtag ที่ทำงานดีกว่าค่ากลาง:\n" + ("\n".join(tags) or "-") +
            "\n\nคลิป outlier (วิว ÷ median ของบัญชี):\n" + ("\n".join(outs) or "-") +
            f"\n\nชั่วโมงที่โพสต์แล้ววิวดีสุด: {hours or '-'}")


def generate(topic: str, niche: str = "", tone: str = "เป็นกันเอง", length_sec: int = 30,
             report: dict | None = None) -> dict:
    prompt = (f"หัวข้อคลิป: {topic}\nกลุ่มเนื้อหา: {niche or '-'}\nโทน: {tone}\n"
              f"ความยาวคลิป: ~{length_sec} วินาที\n\n"
              f"<channel_data>\n{_context_block(report)}\n</channel_data>\n\n"
              "ขอ hook 5 แบบ, caption 3 แบบ, hashtag 1 ชุด (6-8 ตัว), สคริปต์ 1 ชิ้น, "
              "และไอเดียคลิปต่อยอด 5 เรื่อง")
    client = anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise AIError("ANTHROPIC_API_KEY ไม่ถูกต้องหรือยังไม่ได้ตั้ง")
    except anthropic.RateLimitError:
        raise AIError("เรียก Claude ถี่เกินไป ลองใหม่อีกสักครู่")
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise AIError("ต่อ Claude API ไม่ได้ — เช็คอินเทอร์เน็ต")

    if resp.stop_reason == "refusal":
        raise AIError("Claude ปฏิเสธคำขอนี้ ลองปรับหัวข้อ")
    if resp.stop_reason == "max_tokens":
        raise AIError("คำตอบยาวเกินกำหนด ลองหัวข้อที่แคบลง")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise AIError("Claude ตอบกลับมาไม่ใช่ JSON ที่อ่านได้")
