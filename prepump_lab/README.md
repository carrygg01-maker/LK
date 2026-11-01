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
- รายงานขึ้นต้นด้วยตาราง Metrics: First Signal Time · Price at Signal · Breakout Time · Lead Time · Signal Drivers · False Signals · Net Return · Out-of-Sample
- ถ้าไม่มีสัญญาณ จะมีตาราง "ทำไมไม่มีสัญญาณ" บอกว่าแต่ละเงื่อนไขขาดข้อมูล / เฉียดเกณฑ์ / ห่างเกณฑ์
- เปิดไฟล์ `reports/ESPORTSUSDT_replay.html` → เลื่อนเวลาไปก่อน breakout แล้วดูว่า scanner เห็นอะไร
- ⚠️ **OI 5 นาทีผ่าน REST ย้อนหลังได้แค่ ~30 วัน** วันไหนที่ Vision ยังไม่ออกไฟล์ metrics ต้องดึงภายใน 30 วัน ไม่งั้นหายถาวร

### Phase 2–3 — ล็อกการทดลองก่อน แล้วค่อยทดสอบ

```bash
python run.py universe --start 2026-03-01 --end 2026-10-31 --max 120
python run.py lock --name exp1 --train-start 2026-03-01 --val-start 2026-07-01 --test-start 2026-09-01 --test-end 2026-10-31
git add experiments/exp1.json && git commit -m "lock exp1"
python run.py backtest --manifest experiments/exp1.json              # DEV: ใช้ train+validation เท่านั้น ปรับปรุงได้
python run.py backtest --manifest experiments/exp1.json --open-test  # เปิดชุด test — นับจริงแค่ครั้งแรก
```

- `universe` ดึงรายชื่อจาก Vision (**รวมเหรียญที่ delist แล้ว**) แล้วสุ่มลำดับแบบคงที่ ไม่เลือกจาก top gainers
- `lock` สร้าง `experiments/<name>.json` ซึ่งระบุไว้ก่อนเห็นผลว่า:
  - universe คือเหรียญไหนบ้าง
  - design symbols (ค่าเริ่มต้นคือ ESPORTSUSDT) ซึ่งประกาศไว้ชัดเจนและไม่อยู่ใน universe
  - ช่วงเวลา train / validation / test
  - code hash, git commit, config ทั้งหมด และ data hash ของแต่ละเหรียญ
  - สมมติฐานเรื่อง OI lag
- ชุดทดสอบ**ไม่เปลี่ยนตามเหรียญที่เปิดดู** ถ้าเหรียญไหนข้อมูลไม่พร้อม จะไม่ถูกสลับเป็นเหรียญอื่น แต่จะนับรวมใน ready share
- DEV mode ตัดข้อมูลดิบตามเวลาเผยแพร่ก่อน `test_start` ช่วง test จึงไม่ถูกแตะเลย
- `--open-test` เลือกเกณฑ์จาก train+validation ครั้งเดียว แล้วทดสอบช่วง test ครั้งเดียว **ทั้งที่ lag ปกติและ lag อนุรักษนิยม**
  - ทุกครั้งที่เปิด test จะถูกบันทึกต่อท้ายใน `test_runs`
  - เปิดครั้งที่ 2 ขึ้นไป ผลจะเป็น INCONCLUSIVE
  - ถ้าโค้ด, config หรือข้อมูลเปลี่ยนหลังล็อก ผลจะเป็น INCONCLUSIVE

### คำตัดสิน 3 แบบ

| Verdict | เงื่อนไข (ตรวจตามลำดับ) |
|---|---|
| **INCONCLUSIVE** | มี drift / test เคยเปิดแล้ว / เหรียญที่ข้อมูลพร้อม < 80% / นาทีที่ alert เป็น UNKNOWN > 5% / เทรด OOS < 100 |
| **FAIL** | ข้อมูลครบ แต่ไม่ผ่านเกณฑ์ใดเกณฑ์หนึ่ง ที่ lag ปกติ **หรือ** lag อนุรักษนิยม |
| **PASS** | ผ่านทุกเกณฑ์ทั้งสอง lag ในการเปิด test ครั้งแรก |

Forensic ของเหรียญเดียวใช้ 3 ระดับเดียวกัน แต่ PASS ในระดับนี้หมายถึง "ผ่านเฉพาะกรณีนี้" ยังไม่ใช่หลักฐานของกลยุทธ์

### UNKNOWN ≠ FALSE

- feature จะเป็น UNKNOWN ถ้า lookback window ขาดข้อมูลแม้แต่นาทีเดียว เช่น แท่ง 1m หายในช่วง 60 นาทีของ CVD, OI snapshot หาย (ค่าล่าสุดเก่ากว่า 9 นาที) หรือ baseline 24 ชม. มีข้อมูลต่ำกว่า 90%
- ในแต่ละนาที สถานะ alert เป็น ALERT / NO / UNKNOWN โดย UNKNOWN หมายถึง "เงื่อนไขที่รู้ค่าผ่านหมด แต่มีบางข้อที่คำนวณไม่ได้"
- ถ้าไม่มีสัญญาณก่อน breakout แต่มีนาที UNKNOWN ในช่วงนั้น ผลจะเป็น **INCONCLUSIVE ไม่ใช่ FAIL** แม้ coverage ทั้งช่วงจะเป็น 95% ก็ตาม

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

### เกณฑ์ "ผ่าน" ของ walk-forward (ตั้งก่อนรัน — ต้องผ่านทุกข้อ)

| เกณฑ์ | กันอะไร |
|---|---|
| เทรด OOS ≥ 100 | n น้อยจนผลเป็นดวง |
| ขอบล่าง CI 95% ของ Expectancy (หลังต้นทุน) > 0 | ค่าเฉลี่ยบวกแต่ไม่แน่นอน |
| Profit Factor ≥ 1.3 | กำไรบางเกินจะรอดต้นทุนจริงที่สูงกว่าสมมติ |
| ชนะการสุ่มเข้าเทรดจำนวนเท่ากัน | กำไรมาจากตลาดขาขึ้นทั้งตลาด ไม่ใช่จากสัญญาณ |
| เดือนทดสอบที่กำไร ≥ 60% | กำไรมาจากเดือนเดียว |
| ตัด 5% ไม้ที่ดีที่สุดออกแล้ว Expectancy ยังบวก | กำไรพึ่งไม้ฟลุคไม่กี่ไม้ |
| Precision ≥ 20% | ใช้ร่วมกับข้ออื่น ไม่ใช่เกณฑ์เดียว |

รายงานแสดง win rate, กำไรเฉลี่ยไม้ชนะ/ขาดทุนเฉลี่ยไม้แพ้, payoff ratio และ win rate คุ้มทุน เพื่อดูว่า precision ต่ำยังคุ้มหรือไม่

### OI ใช้ได้เมื่อไหร่

Binance ไม่ได้ระบุในเอกสารว่า OI 5 นาทีพร้อมใช้งานช้ากว่า timestamp กี่นาที ระบบจึงจัดการแบบนี้:

1. ค่าเริ่มต้นตั้ง lag ไว้ 5 นาที
2. `forensic` รันซ้ำด้วย lag 0/5/10/15/30 นาที แล้วรายงานว่าผลเปลี่ยนไปแค่ไหน
3. วัดค่าจริง (เครื่องต้องเปิด NTP; ระบบเทียบนาฬิกากับ `/fapi/v1/time` ทุกรอบและแก้ offset ให้ในแต่ละแถว) ด้วย `python run.py oi-latency --symbol ESPORTSUSDT --hours 48` ซึ่งจะ poll ทั้ง API ย้อนหลังและ API real-time แล้วบันทึกว่า
   - แถวใหม่ปรากฏช้ากว่า timestamp กี่นาที
   - ค่าในแถวตรงกับ OI real-time ณ ต้นช่วงหรือปลายช่วง 5 นาที

   รันอย่างน้อย 24–72 ชั่วโมง (ค่าเริ่มต้น `--hours 48`) ระบบบันทึกลง CSV ทันทีที่เจอแถวใหม่ กด Ctrl+C แล้วรันต่อได้
   สรุปผลด้วย `python run.py oi-latency-summary --symbol ESPORTSUSDT` ระบบจะแนะนำ `oi_lag_min` = p99 + ความละเอียดการ poll + 1 นาที

### data_quality_report.json

`fetch` / `forensic` / `backtest` เขียนรายงานคุณภาพข้อมูลทุกครั้ง แต่ละเหรียญมี coverage, ช่วงที่ขาด (gap), สัดส่วนแท่ง volume=0 และสถานะ `ready` / `partial` / `not_ready`
- backtest ตัดเหรียญ `not_ready` ออกอัตโนมัติ
- ข้อมูลที่ขาด (แท่ง 1m หาย, OI หายเกิน 15 นาที) เป็น **NaN ไม่ใช่ 0 หรือค่าเก่า** — ไม่อย่างนั้นข้อมูลขาดจะดูเหมือน "ตลาดเงียบ / OI นิ่ง" แล้วถูกโทษว่าตลาดไม่มีแรง
- ตาราง "ทำไมไม่มีสัญญาณ" จัดลำดับสาเหตุ: ข้อมูลขาด (coverage < 80%) > เกณฑ์เข้มเกิน > ตลาดไม่มีแรง

### กัน ESPORTS รั่วเข้า Out-of-Sample

- ใช้ `design_symbols` ใน manifest ที่ประกาศไว้ล่วงหน้า (ไม่ตัดเหรียญตามไฟล์ forensic ที่เปิดดู — แบบนั้นทำให้ชุดทดสอบเปลี่ยนตามการใช้งาน)
- CI ใช้สองวิธี (cluster bootstrap รายวัน + moving-block bootstrap ตามเวลา) แล้วตัดสินด้วยขอบล่างที่ต่ำกว่า

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
