# TikTok Lab — เครื่องมือวิเคราะห์ TikTok ของเราเอง (แนว vidIQ)

แดชบอร์ดบนเครื่องตัวเอง ดึงข้อมูลผ่าน **TikTok API ทางการ** แล้วตอบ 4 คำถาม:

| แท็บ | ตอบคำถามว่า | ใช้ API |
|---|---|---|
| **ช่องของฉัน** | คลิปไหนทำได้เกินมาตรฐานตัวเอง, ควรโพสต์วัน/เวลาไหน, hashtag ไหนได้ผล, ช่องโตหรือหด | Display API (OAuth ของเรา) |
| **คู่แข่ง + Outliers** | คู่แข่งมีคลิปไหนที่ "แตก" ผิดปกติ และใช้แท็กอะไร | Research API |
| **Hashtag** | แท็กนี้มีคนใช้คู่กับแท็กอะไร, แท็กไหนดันวิว, คลิปท็อปในแท็กหน้าตาเป็นยังไง | Research API |
| **AI ช่วยเขียน** | hook / caption / hashtag / สคริปต์ — โดยใช้ข้อมูลจริงของช่องเป็นบริบท | Claude API |

ลองได้ทันทีโดยไม่ต้องสมัครอะไร:

```bash
cd tiktok_lab
pip install -r requirements.txt
python webapp/app.py --demo
# เปิด http://localhost:5001
```

โหมด demo ใช้ข้อมูลจำลองที่ฝังรูปแบบไว้ (แท็ก #สูตรอาหาร ดันวิว, โพสต์ 19-22 น. ดีกว่า)
ถ้าหน้าเว็บหารูปแบบพวกนี้เจอ แปลว่าตัววิเคราะห์ทำงานถูก

---

## ตัวเลขแต่ละตัวคำนวณยังไง

- **median ไม่ใช่ mean** — ยอดวิว TikTok หางยาวมาก คลิปไวรัลคลิปเดียวทำค่าเฉลี่ยพัง
- **Outlier score** = วิวคลิปนั้น ÷ median วิวของ *บัญชีเดียวกัน* · ≥ 3x ถือว่าเป็น outlier
  เทียบกับเจ้าของเอง ไม่ใช่เทียบข้ามบัญชี → บัญชีเล็กกับใหญ่เทียบกันได้
- **Engagement rate (ER)** = (ไลก์ + คอมเมนต์ + แชร์) ÷ วิว
- **Hashtag lift** = median วิวของคลิปที่ใช้แท็ก ÷ median วิวทั้งหมด (> 1 = ดีกว่าค่ากลาง)
  ต้องมีอย่างน้อย 3 คลิปถึงจะแสดง · แท็กกว้างอย่าง #fyp ถูกทำเครื่องหมายไว้
- **เวลาโพสต์** — heatmap median วิว ตามวัน × ช่วง 3 ชม. (เวลาไทย, เปลี่ยนได้ด้วย `TIKTOK_TZ`)
  ช่องที่มีคลิปน้อยกว่า 3 คลิปจะเว้นว่าง

ข้อควรระวัง: ทั้งหมดนี้เป็น **ความสัมพันธ์ ไม่ใช่เหตุและผล** — แท็กที่ lift สูงอาจเป็นเพราะ
ใช้กับหัวข้อที่ดีอยู่แล้ว ใช้เป็นสมมติฐานไปทดลอง ไม่ใช่สูตรสำเร็จ

---

## ใช้กับข้อมูลจริง

### 1. สร้างแอปบน TikTok for Developers

1. เข้า <https://developers.tiktok.com/> → **Manage apps** → **Connect an app**
2. เพิ่ม product **Login Kit** และ scopes: `user.info.basic`, `user.info.profile`, `user.info.stats`, `video.list`
3. ตั้ง Redirect URI เป็น `http://localhost:5001/auth/callback` (หรือโดเมนจริงของคุณ)
4. ส่งแอปให้ TikTok review — ระหว่างรอ ใช้ **Sandbox** ได้กับบัญชีที่เพิ่มเป็น target user

### 2. (ถ้าต้องการแท็บคู่แข่ง/Hashtag) ขอ Research API

Research API ให้ข้อมูลสาธารณะของบัญชีอื่นและ hashtag แต่ **ต้องยื่นขอและได้รับอนุมัติ**
(<https://developers.tiktok.com/products/research-api/>) ปัจจุบันเปิดให้นักวิจัยในบางประเทศ
และมีโควตาประมาณ 1,000 request/วัน — ตัวแอปแคชผลไว้ 1 ชม. เพื่อประหยัดโควตา

ถ้าไม่ได้รับอนุมัติ แท็บ "ช่องของฉัน" และ "AI ช่วยเขียน" ยังใช้ได้ตามปกติ

### 3. ตั้งค่าแล้วรัน

```bash
export TIKTOK_CLIENT_KEY=xxxxxxxx
export TIKTOK_CLIENT_SECRET=xxxxxxxx
export TIKTOK_REDIRECT_URI=http://localhost:5001/auth/callback   # ต้องตรงกับที่ตั้งในแอป
export TIKTOK_RESEARCH=1          # เฉพาะเมื่อได้รับอนุมัติ Research API
export ANTHROPIC_API_KEY=sk-ant-...   # สำหรับแท็บ AI
export FLASK_SECRET=$(python -c "import secrets;print(secrets.token_hex(16))")

python webapp/app.py
```

เปิดหน้าเว็บ กด **เชื่อมบัญชี** → ล็อกอิน TikTok → กลับมาเห็นข้อมูลช่องตัวเอง
token เก็บไว้ที่ `tiktok_lab/.tokens.json` (สิทธิ์ 600, อยู่ใน .gitignore) และ refresh อัตโนมัติ

---

## ไฟล์

```
tiktok_lab/
├── tiktok_api.py   ตัวเชื่อม TikTok API (OAuth, Display API, Research API)
├── demo.py         แหล่งข้อมูลจำลอง หน้าตาเหมือน LiveSource
├── analytics.py    outlier, ER, เวลาโพสต์, hashtag lift, แนวโน้ม, ความยาวคลิป
├── ai_writer.py    Claude สร้าง hook/caption/hashtag/สคริปต์ (structured JSON output)
└── webapp/
    ├── app.py              Flask server
    └── templates/index.html
```

## สิ่งที่ตั้งใจไม่ทำ

- **ไม่ scrape หน้าเว็บ TikTok** — ผิดเงื่อนไขการใช้งาน เสี่ยงโดนบล็อก และพังทุกครั้งที่เว็บเปลี่ยน
- **ไม่มีข้อมูล search volume ของคีย์เวิร์ด** — TikTok ไม่เปิด API นี้ให้ภายนอก
  แท็บ Hashtag ใช้ "จำนวนคลิป × lift" จาก Research API แทน
