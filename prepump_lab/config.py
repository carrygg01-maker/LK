"""
config.py — ทุกเกณฑ์ตั้งไว้ที่นี่ "ก่อนเห็นผล"

กฎ: ถ้าแก้ค่าในไฟล์นี้หลังดูผล ESPORTS แล้ว ต้องบันทึกใน CHANGELOG ของรายงาน
และผล ESPORTS จะกลายเป็น in-sample ทันที (ใช้พิสูจน์อะไรไม่ได้อีก)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace


@dataclass(frozen=True)
class SignalCfg:
    # --- OI Expansion: มีสถานะใหม่เข้ามาขณะที่ราคายังไม่วิ่ง
    oi_window: int = 60            # นาที
    oi_min_chg: float = 0.05       # OI เพิ่ม >= 5% ใน 60 นาที
    # --- CVD (taker buy - taker sell, หน่วย USDT) z-score เทียบ 24 ชม.ย้อนหลัง
    cvd_window: int = 60
    cvd_z: float = 2.0
    # --- Relative Volume: volume 15 นาทีล่าสุด / median ของ 24 ชม.ก่อนหน้า
    rv_window: int = 15
    rv_min: float = 3.0
    # --- Price Extension: ต้องยังไม่วิ่งไปไกล (ถ้าวิ่งไปแล้วคือ "ตามหลัง" ไม่ใช่ "ก่อน")
    ext_window: int = 60
    ext_max: float = 0.03          # ราคาขึ้นใน 60 นาทีต้อง < 3%
    # --- baseline
    baseline: int = 1440           # 24 ชม. สำหรับ z-score / median
    # --- composite rule: ต้องผ่าน OI + RV + Extension และ CVD อย่างน้อยหนึ่งตลาด
    require_spot_cvd: bool = False
    cooldown: int = 120            # นาที ระหว่าง alert สองครั้งในเหรียญเดียวกัน


@dataclass(frozen=True)
class DataCfg:
    # OI (metrics 5m) ไม่รู้แน่ว่าเผยแพร่ช้ากี่นาที -> สมมติแบบระวัง
    # ค่า create_time = T จะถือว่า "เห็นได้" ตั้งแต่ T + oi_lag_min
    oi_lag_min: int = 5
    # aggTrades: นับเป็น "ไม้ใหญ่" เมื่อมูลค่า >= ค่านี้ (USDT)
    big_trade_usd: float = 20_000


@dataclass(frozen=True)
class LabelCfg:
    """นิยาม 'พุ่ง' — ใช้ประเมินผลเท่านั้น ห้ามส่งเข้า signal"""
    pump_gain: float = 0.30        # จุดต่ำ -> จุดสูง >= 30%
    pump_window: int = 1440        # ภายใน 24 ชม.
    breakout_gain: float = 0.05    # breakout = close แรกที่ >= low * 1.05
    lead_window: int = 720         # alert ภายใน 12 ชม.ก่อน breakout = "นำหน้า"


@dataclass(frozen=True)
class TradeCfg:
    fee_per_side: float = 0.0005   # taker 0.05%
    slip_per_side: float = 0.0010  # 10 bps — เหรียญเล็กช่วงเริ่มพุ่ง slippage สูง ห้ามใช้ 1bp
    take_profit: float = 0.15
    stop_loss: float = 0.05
    max_hold: int = 720            # นาที


@dataclass(frozen=True)
class Cfg:
    signal: SignalCfg = field(default_factory=SignalCfg)
    data: DataCfg = field(default_factory=DataCfg)
    label: LabelCfg = field(default_factory=LabelCfg)
    trade: TradeCfg = field(default_factory=TradeCfg)

    def to_dict(self) -> dict:
        return asdict(self)

    def with_signal(self, **kw) -> "Cfg":
        return replace(self, signal=replace(self.signal, **kw))


DEFAULT = Cfg()

# grid เล็กๆ สำหรับ walk-forward (ยิ่ง grid ใหญ่ ยิ่ง overfit ง่าย — อย่าขยายโดยไม่จำเป็น)
WF_GRID = {
    "oi_min_chg": [0.03, 0.05, 0.08],
    "cvd_z": [1.5, 2.0, 2.5],
    "rv_min": [2.0, 3.0, 4.0],
}
