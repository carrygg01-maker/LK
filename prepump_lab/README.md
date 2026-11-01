# Pre-Pump Forensic Lab

ใช้ตอบคำถามเดียว: **"เราตรวจจับเหรียญก่อนพุ่งแบบ ESPORTSUSDT ได้จริงไหม และทำซ้ำกับเหรียญอื่นได้แค่ไหน"**

Output ที่ได้คือ **Interactive Replay Dashboard** (HTML ไฟล์เดียว เปิด offline ได้) + **รายงาน Forensic/Backtest** (Markdown + JSON + CSV)
ยังไม่มี Scanner real-time — จะทำก็ต่อเมื่อ Phase 1–3 ผ่านแล้ว

---

## รันบนเครื่องคุณ (Ryzen 5 5500 / 16GB เพียงพอ)

```bash
cd prepump_lab
pip install -r requirements.txt
python run.py selftest
```

`selftest` ตรวจเครื่องมือกับข้อมูลสังเคราะห์ที่รู้คำตอบอยู่แล้ว **ต้องผ่านก่อนเชื่อผลจากข้อมูลจริง**

### Phase 1 — Forensic Replay: ESPORTS

```bash
python run.py fetch --symbol ESPORTSUSDT --start 2026-10-18 --end 2026-11-02 --aggtrades
python run.py forensic --symbol ESPORTSUSDT --focus-start 2026-10-25 --focus-end 2026-11-02
```

- ดึงข้อมูลเริ่มที่ **18 ต.ค.** ไม่ใช่ 25 ต.ค. เพราะต้องใช้ 24 ชม.แรกเป็น baseline และใช้อีก 6 วันนับ false signal ก่อนเหตุการณ์
- เปิดไฟล์ `reports/ESPORTSUSDT_replay.html` → เลื่อนเวลาไปก่อน breakout แล้วดูว่า scanner เห็นอะไร
- ⚠️ **OI 5 นาทีผ่าน REST ย้อนหลังได้แค่ ~30 วัน** วันไหนที่ Vision ยังไม่ออกไฟล์ metrics ต้องดึงภายใน 30 วัน ไม่งั้นหายถาวร

### Phase 2–3 — หลายเหรียญ + Walk-Forward

```bash
python run.py universe --start 2026-03-01 --end 2026-10-31 --max 120
python run.py backtest --exclude ESPORTSUSDT
```

- `universe` ดึงรายชื่อจาก Vision (**รวมเหรียญที่ delist แล้ว**) แล้วสุ่มลำดับคงที่ — ไม่เลือกจาก top gainers
- `--exclude ESPORTSUSDT` เพราะเหรียญที่ใช้ออกแบบสูตรห้ามอยู่ในชุดทดสอบ
- 1m klines 120 เหรียญ × 8 เดือน ≈ 40M แถว ใช้ disk ~3–5 GB (ไม่ดึง aggTrades ใน Phase 2)

---

## Replay Dashboard

| ส่วน | ทำอะไร |
|---|---|
| Replay slider / ▶ / ←→ | เลื่อนเวลา กราฟวาดเฉพาะข้อมูลที่เผยแพร่แล้ว ณ เวลานั้น ส่วนอนาคตถูกบัง |
| Scanner เห็นอะไร | ค่า OI / CVD / Volume / Extension ณ นาทีที่เลือก เทียบเกณฑ์ ผ่าน/ไม่ผ่าน |
| Alert ที่เกิดแล้ว | เฉพาะ alert ที่เกิดก่อนเวลาที่เลือก ประเภทเป็น `?` จนกว่าจะกดเฉลย |
| ปุ่ม "เฉลย" | เปิดราคาอนาคต + จุด breakout/peak + ประเภท alert (lead / late / false) |
| คำตอบ 4 ข้อ | Q1 สัญญาณแรก · Q2 นำหน้ากี่นาที · Q3 false ก่อนหน้า · Q4 ผลเทรดจริงหลังต้นทุน |
| ข้อมูลไหนขยับก่อน | ลำดับ stream ที่ติดก่อน breakout คู่กับอัตราติดในวันปกติ |

---

## กลไกกันการหลอกตัวเอง

| ความเสี่ยง | ป้องกันด้วย |
|---|---|
| Look-ahead bias | แถว t = ตัดสินใจตอน t+1m · OI ใช้ได้หลัง `create_time + oi_lag_min` · `test_truncation_equivalence` คำนวณซ้ำจากข้อมูลที่ตัดตามเวลาเผยแพร่ 25 จุดสุ่ม แล้วต้องได้ค่าเท่ากันทุกตัว |
| ใช้เฉลยในสัญญาณ | `labels.py` (ใช้อนาคต) ห้าม import ใน `features.py`/`signals.py` — test ตรวจ AST |
| ราคาเข้าที่ทำไม่ได้จริง | เข้าที่ **open แท่งถัดไป** + slippage 10 bps + fee 5 bps ต่อข้าง · TP/SL แท่งเดียวกัน = โดน SL ก่อน |
| Selection bias | universe สุ่มจากทุกเหรียญใน Vision รวม delist · ไม่กรองตามผลงาน |
| Overfit เกณฑ์ | grid เล็ก 27 ชุด · walk-forward รายเดือน เลือกจากอดีตอย่างเดียว · เทรด train ต้องปิดก่อนเดือนทดสอบ |
| ผลดีเพราะดวง | เทียบกับการสุ่มเข้าเทรดจำนวนเท่ากัน · CI แบบ cluster bootstrap รายวัน |
| ขยับเกณฑ์หลังเห็นผล | เกณฑ์ทั้งหมดอยู่ใน `config.py` + เกณฑ์ผ่านใน `backtest.py` ตั้งก่อนรัน |

### นิยาม (ใน `config.py`)

- **พุ่ง**: low → peak ≥ 30% ภายใน 24 ชม.
- **Breakout**: close แรกหลังจุด low ที่ ≥ low × 1.05 — lead time วัดเทียบจุดนี้
- **Lead alert**: เกิดภายใน 12 ชม.ก่อน breakout · **Late**: ระหว่าง breakout–peak · **False**: อื่นๆ
- **Alert**: OI +5%/60m **และ** CVD (spot หรือ futures) z ≥ 2 **และ** volume 15m ≥ 3× median 24 ชม. **และ** ราคา 60m ยังขึ้นไม่ถึง 3%

### เกณฑ์ "ผ่าน" ของ walk-forward (ตั้งก่อนรัน)

เทรด OOS ≥ 100 · ขอบล่าง CI 95% ของกำไรเฉลี่ย > 0 · ชนะการสุ่ม · Precision ≥ 20%

---

## ผล selftest (ข้อมูลสังเคราะห์)

| ทดสอบ | ฝังไว้ | วัดได้ |
|---|---|---|
| Lead time | build-up 90 นาทีก่อนเริ่มพุ่ง | สัญญาณแรก 88 นาทีก่อน breakout ✓ |
| Decoy (สะสมแต่ไม่พุ่ง) | 2 ครั้ง | false alert 2 ครั้ง ✓ |
| พุ่งแบบไม่มีอะไรนำ | 1 ครั้ง | ไม่มี lead alert ✓ |
| Random walk ล้วน | ไม่มี edge | ไม่ผ่าน ✓ |
| ตัดข้อมูลตามเวลาเผยแพร่ 25 จุด | — | ค่าเท่ากันทุกจุด ✓ |

---

## ข้อจำกัดที่รู้แล้ว

- **Liquidations**: Binance Vision ไม่มีย้อนหลัง (ต้องเก็บจาก websocket `forceOrder` เอง เริ่มจากวันนี้)
- **Order Book L2**: ไม่มีในข้อมูลฟรี — ถ้า Phase 1 ชี้ว่า flow เริ่มก่อน OI/CVD จะเห็น ค่อยพิจารณา Tardis.dev
- **เวลาเผยแพร่ OI จริง** ไม่มีเอกสารยืนยัน — ใช้ lag 5 นาทีแบบระวัง ลองรันด้วย 10–15 นาที ถ้าผลเปลี่ยนมากแปลว่าผลขึ้นกับสมมติฐานนี้
- เหรียญที่ไม่มีตลาด spot จะไม่มี Spot CVD (dashboard ขึ้น "ไม่มีข้อมูล")
- ข้อมูลตลาดบอกได้ว่า "ใครซื้อก่อน" ไม่ได้บอก "ทำไม" — ต้องเทียบ timeline ข่าว/ประกาศแยกเอง
