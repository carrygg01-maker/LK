#!/usr/bin/env python3
"""เช็กว่ากองทุน/CTA (Managed Money) เปิด Short ทองคำ COMEX จริงไหม จากรายงาน COT ของ CFTC

ใช้งาน:
    python gold_cot.py              # ดู 8 สัปดาห์ล่าสุด
    python gold_cot.py --weeks 12   # ดูกี่สัปดาห์ก็ได้

ใช้แค่ Python 3 ไม่ต้องลงไลบรารีเพิ่ม
ข้อมูล: CFTC Disaggregated Futures-Only (ประกาศทุกวันศุกร์ ข้อมูล ณ วันอังคาร)
"""
import argparse
import json
import urllib.parse
import urllib.request

API = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"  # Disaggregated Futures-Only
GOLD_CODE = "088691"  # GOLD - COMMODITY EXCHANGE INC. (COMEX)


def fetch(weeks):
    """ดึงข้อมูลทองคำล่าสุด `weeks` สัปดาห์ (ใหม่ -> เก่า)"""
    params = {
        "$where": f"cftc_contract_market_code='{GOLD_CODE}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(weeks),
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "gold-cot-check/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def num(row, key):
    return float(row.get(key) or 0)


def signal(d_short, d_oi):
    """ตีความการเปลี่ยนแปลง Short ของ Managed Money เทียบกับ Open Interest"""
    if d_short > 0 and d_oi > 0:
        return "เปิด Short ใหม่เพิ่ม (มีเงินใหม่เข้ามากด)"
    if d_short > 0 and d_oi <= 0:
        return "Short เพิ่มแต่ OI ลด (สัญญาเดิมถูกปิด/สลับ ไม่ใช่เงินใหม่ล้วน)"
    if d_short <= 0 and d_oi <= 0:
        return "ปิด Short / ลดแรงกด (Short covering)"
    return "Short ลดแต่ OI เพิ่ม (มีเงินใหม่ฝั่ง Long หรือ Spread)"


def analyze(rows):
    """แปลงแถว CFTC เป็นลิสต์เรียงจากเก่า -> ใหม่ พร้อมค่าที่คำนวณแล้ว"""
    rows = sorted(rows, key=lambda r: r["report_date_as_yyyy_mm_dd"])
    out, prev = [], None
    for r in rows:
        long_ = num(r, "m_money_positions_long_all")
        short = num(r, "m_money_positions_short_all")
        oi = num(r, "open_interest_all")
        item = {
            "date": r["report_date_as_yyyy_mm_dd"][:10],
            "long": long_,
            "short": short,
            "net": long_ - short,
            "oi": oi,
            "d_short": None,
            "d_oi": None,
            "signal": "-",
        }
        if prev is not None:
            item["d_short"] = short - prev["short"]
            item["d_oi"] = oi - prev["oi"]
            item["signal"] = signal(item["d_short"], item["d_oi"])
        out.append(item)
        prev = item
    return out


def fmt_change(v):
    return "-" if v is None else f"{v:+,.0f}"


def print_report(data):
    print(f"{'วันที่':<12}{'MM Long':>12}{'MM Short':>12}{'MM Net':>12}"
          f"{'OI':>12}{'Δ Short':>10}{'Δ OI':>10}  สัญญาณ")
    print("-" * 120)
    for d in data:
        print(f"{d['date']:<12}{d['long']:>12,.0f}{d['short']:>12,.0f}{d['net']:>12,.0f}"
              f"{d['oi']:>12,.0f}{fmt_change(d['d_short']):>10}{fmt_change(d['d_oi']):>10}  {d['signal']}")

    last = data[-1]
    print()
    print(f"สรุปล่าสุด ({last['date']}): Managed Money Short {last['short']:,.0f} สัญญา "
          f"({fmt_change(last['d_short'])} จากสัปดาห์ก่อน)")
    print(f"สัญญาณ: {last['signal']}")
    print()
    print("หมายเหตุ: COT เป็นข้อมูลรายสัปดาห์ (ล่าช้า ~3 วัน) และรวมกองทุน/CTA ทั้งหมดไว้ด้วยกัน")
    print("ไม่ได้บอกว่าใครเป็นคนเปิด และ Short อย่างเดียวยังไม่พิสูจน์ว่า 'กดราคา' โดยตรง")


def main():
    parser = argparse.ArgumentParser(description="เช็ก Managed Money Short ทองคำ COMEX จาก CFTC COT")
    parser.add_argument("--weeks", type=int, default=8, help="จำนวนสัปดาห์ที่จะดู (ค่าเริ่มต้น 8)")
    args = parser.parse_args()

    rows = fetch(max(args.weeks, 2))  # ต้องมีอย่างน้อย 2 สัปดาห์ถึงจะคำนวณการเปลี่ยนแปลงได้
    if len(rows) < 2:
        raise SystemExit("ดึงข้อมูลได้น้อยกว่า 2 สัปดาห์ ตรวจสอบการเชื่อมต่อหรือรหัสสัญญา")
    print_report(analyze(rows))


if __name__ == "__main__":
    main()
