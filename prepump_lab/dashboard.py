"""
dashboard.py — Replay Dashboard แบบไฟล์ HTML เดียว (เปิดได้ offline ไม่ต้องมี server)

Replay mode (ค่าเริ่มต้น): กราฟแสดงเฉพาะข้อมูลที่เผยแพร่แล้ว ณ เวลาที่เลื่อน
  - ไม่แสดง breakout/peak (มาจาก labels.py = กระดาษคำตอบที่ใช้อนาคต)
  - ค่าในแผงหลักฐานคือค่าที่ scanner เห็นจริง ณ นาทีนั้น
กด "เฉลย" เพื่อเปิดเส้นอนาคตและจุด breakout
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import signals


def _arr(s: pd.Series, nd: int = 6) -> list:
    out = []
    for v in s.to_numpy(dtype=float):
        out.append(None if not math.isfinite(v) else round(v, nd))
    return out


def payload(res: dict, cfg, backtest: dict | None = None) -> dict:
    b, comp = res["bars"], res["components"]
    a = res["answers"]
    t0 = b.index[0]
    p0 = float(b["close"].iloc[0])
    data = {
        "symbol": a["symbol"],
        "t0": int(t0.timestamp() * 1000),
        "n": len(b),
        "close": _arr(b["close"], 8),
        "oi_chg": _arr(b["oi_chg"] * 100, 2),
        "fut_cvd_z": _arr(b["fut_cvd_z"], 2),
        "spot_cvd_z": _arr(b["spot_cvd_z"], 2),
        "rvol": _arr(b["rvol"], 2),
        "ret_ext": _arr(b["ret_ext"] * 100, 2),
        "pressure": _arr(signals.pressure(comp), 0),
        "comp": {c: comp[c].astype(int).tolist() for c in signals.COMPONENTS},
        "alerts": [int((t - t0) / pd.Timedelta(minutes=1)) for t in res["alerts"]],
        "alert_kind": res["classes"]["kind"].tolist() if len(res["classes"]) else [],
        "episodes": [{"low": int((e.low_time - t0) / pd.Timedelta(minutes=1)),
                      "br": int((e.breakout_time - t0) / pd.Timedelta(minutes=1)),
                      "peak": int((e.peak_time - t0) / pd.Timedelta(minutes=1)),
                      "gain": round(e.gain * 100, 1)} for e in res["episodes"]],
        "answers": json.loads(json.dumps(a, default=str)),
        "thresholds": cfg.to_dict()["signal"],
        "backtest": backtest,
        "p0": p0,
    }
    return data


def write(res: dict, cfg, path: Path, backtest: dict | None = None) -> Path:
    data = payload(res, cfg, backtest)
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False, default=str))
    html = html.replace("__TITLE__", f"{data['symbol']} Forensic Replay")
    path.write_text(html, encoding="utf-8")
    return path


TEMPLATE = r"""<!doctype html>
<html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1b1d21;--muted:#6b7079;--line:#e3e4e8;--grid:#eef0f3;
--price:#1f4fd1;--pres:#d97706;--oi:#7c3aed;--fut:#0e9f6e;--spot:#0891b2;--alert:#dc2626;--ok:#0e9f6e;--bad:#9ca3af;--future:#c7cad1}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111317;--panel:#1a1d23;--ink:#e8e9ec;--muted:#9097a3;--line:#2a2e36;--grid:#22262d;
--price:#7aa2ff;--pres:#f59e0b;--oi:#a78bfa;--fut:#34d399;--spot:#22d3ee;--alert:#f87171;--ok:#34d399;--bad:#5b616c;--future:#3a3f48}}
:root[data-theme="dark"]{--bg:#111317;--panel:#1a1d23;--ink:#e8e9ec;--muted:#9097a3;--line:#2a2e36;--grid:#22262d;
--price:#7aa2ff;--pres:#f59e0b;--oi:#a78bfa;--fut:#34d399;--spot:#22d3ee;--alert:#f87171;--ok:#34d399;--bad:#5b616c;--future:#3a3f48}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI","Noto Sans Thai",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:20px;margin:0}h2{font-size:15px;margin:0 0 10px}.sub{color:var(--muted);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:16px 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.kpi span{color:var(--muted);font-size:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:12px}
.grid2{display:grid;grid-template-columns:2.2fr 1fr;gap:12px}@media(max-width:860px){.grid2{grid-template-columns:1fr}}
canvas{width:100%;height:440px;display:block}
.ctrl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}
input[type=range]{flex:1;min-width:180px}button{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 10px;cursor:pointer;font:inherit}
button.on{border-color:var(--alert);color:var(--alert)}.clock{font-variant-numeric:tabular-nums;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:left}
td.num{text-align:right;font-variant-numeric:tabular-nums}.pass{color:var(--ok);font-weight:600}.fail{color:var(--bad)}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted)}.legend i{display:inline-block;width:10px;height:3px;margin-right:4px;vertical-align:middle}
.alertlist{max-height:220px;overflow:auto}.tag{font-size:11px;padding:1px 6px;border-radius:6px;border:1px solid var(--line)}
.note{font-size:12px;color:var(--muted)}pre{white-space:pre-wrap;font-size:12px}
</style></head><body><div class="wrap">
<h1 id="ttl"></h1><div class="sub" id="range"></div>
<div class="kpis" id="kpis"></div>
<div class="grid2">
 <div class="card"><div class="legend">
  <span><i style="background:var(--price)"></i>ราคา</span><span><i style="background:var(--pres)"></i>Signal pressure</span>
  <span><i style="background:var(--oi)"></i>OI Δ60m %</span><span><i style="background:var(--fut)"></i>Fut CVD z</span>
  <span><i style="background:var(--spot)"></i>Spot CVD z</span><span><i style="background:var(--alert)"></i>Alert</span></div>
  <canvas id="cv"></canvas>
  <div class="ctrl"><button id="play">▶ เล่น</button><button id="back">−1 ชม.</button><button id="fwd">+1 ชม.</button>
   <input type="range" id="sl"><span class="clock" id="clk"></span>
   <button id="reveal" title="เปิดข้อมูลอนาคต + จุด breakout (กระดาษคำตอบ)">เฉลย: ปิด</button>
   <button id="jump">ไปสัญญาณแรก</button></div>
  <div class="note" style="margin-top:6px">Replay mode: แสดงเฉพาะข้อมูลที่เผยแพร่แล้ว ณ เวลาที่เลือก (แท่ง 1m เห็นหลังปิด, OI เห็นหลัง create_time + lag) · ซูม: ลากเลือกช่วงบนกราฟ ดับเบิลคลิกเพื่อรีเซ็ต</div>
 </div>
 <div>
  <div class="card"><h2>Scanner เห็นอะไร ณ <span id="clk2"></span></h2><table id="ev"></table>
   <div class="note" id="verdictNow" style="margin-top:8px"></div></div>
  <div class="card"><h2>Alert ที่เกิดแล้ว</h2><div class="alertlist"><table id="al"></table></div></div>
 </div>
</div>
<div class="card"><h2>คำตอบ Forensic 4 ข้อ</h2><table id="qa"></table></div>
<div class="card"><h2>ข้อมูลไหนขยับก่อน</h2><table id="fm"></table>
 <div class="note">ดู "นำหน้า" คู่กับ "อัตราติดปกติ/วัน" เสมอ — ขยับก่อนแต่ติดบ่อยทุกวัน = เสียงรบกวน</div></div>
<div class="card" id="btcard" style="display:none"><h2>Walk-Forward Backtest (Out-of-Sample)</h2><div id="bt"></div></div>
<div class="card"><h2>เกณฑ์ที่ใช้ (ตั้งก่อนรัน)</h2><pre id="th"></pre></div>
</div>
<script>
const D=__DATA__;
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const MIN=60000, fmtT=i=>{const d=new Date(D.t0+i*MIN);return d.toISOString().slice(0,16).replace('T',' ')+' UTC'};
const A=D.answers;
document.getElementById('ttl').textContent=D.symbol+' — Pre-Pump Forensic Replay';
document.getElementById('range').textContent='ข้อมูล '+A.data_start+' → '+A.data_end+(D.symbol.startsWith('SYN')?' · ข้อมูลสังเคราะห์สำหรับทดสอบเครื่องมือ ไม่ใช่ตลาดจริง':' · Binance USDⓈ-M + Spot');
const k=(v,l)=>`<div class="kpi"><b>${v}</b><span>${l}</span></div>`;
const t4=A.Q4_trade_on_first_signal;
document.getElementById('kpis').innerHTML=
 k(A.main_episode?('+'+A.main_episode.gain_pct+'%'):'-','การพุ่งหลัก (low→peak)')+
 k(A.Q2_lead_min!=null?Math.round(A.Q2_lead_min)+' นาที':'ไม่มี','สัญญาณนำหน้า breakout')+
 k(A.Q3_false_before!=null?A.Q3_false_before:'-','False alert ก่อนหน้า ('+(A.Q3_days_observed||'-')+' วัน)')+
 k(t4?((t4.net_pct>=0?'+':'')+t4.net_pct.toFixed(2)+'%'):'-','เทรดตามสัญญาณแรก (สุทธิ)')+
 k(A.all_trades_summary.n?((A.all_trades_summary.mean_net_pct>=0?'+':'')+A.all_trades_summary.mean_net_pct.toFixed(2)+'%'):'-','เฉลี่ยทุก alert ('+(A.all_trades_summary.n||0)+' ไม้)');
const cv=document.getElementById('cv'),ctx=cv.getContext('2d'),sl=document.getElementById('sl');
sl.min=Math.min(D.n-1,1440);sl.max=D.n-1;
let cur=A.Q1_first_signal?Math.max(+sl.min,D.alerts[0]-120):Math.floor(D.n*0.6);
if(A.main_episode){const br=D.episodes.find(e=>fmtT(e.br).startsWith(A.main_episode.breakout_time.slice(0,16).replace('T',' ')));if(br)cur=Math.max(+sl.min,br.br-240);}
let reveal=false,view=null,drag=null,timer=null;
function rng(){return view||[Math.max(0,cur-2880),Math.min(D.n-1,cur+720)]}
function size(){const r=cv.getBoundingClientRect(),d=devicePixelRatio||1;cv.width=r.width*d;cv.height=r.height*d;ctx.setTransform(d,0,0,d,0,0)}
function draw(){
 size();const W=cv.clientWidth,H=cv.clientHeight,L=8,R=W-46;const [a,b]=rng();
 const X=i=>L+(i-a)/(b-a)*(R-L);ctx.clearRect(0,0,W,H);ctx.font='11px system-ui';
 const panes=[{y:0,h:H*0.46},{y:H*0.49,h:H*0.17},{y:H*0.69,h:H*0.29}];
 const lim=reveal?b:Math.min(b,cur);
 function line(arr,p,col,lo,hi,dash,upTo,from){ctx.strokeStyle=col;ctx.lineWidth=1.4;ctx.setLineDash(dash||[]);ctx.beginPath();let pen=false;
  for(let i=from??a;i<=upTo;i++){const v=arr[i];if(v==null){pen=false;continue}const y=p.y+p.h-(v-lo)/(hi-lo||1)*p.h;const x=X(i);pen?ctx.lineTo(x,y):ctx.moveTo(x,y);pen=true}
  ctx.stroke();ctx.setLineDash([])}
 function ext(arr,upTo){let lo=Infinity,hi=-Infinity;for(let i=a;i<=upTo;i++){const v=arr[i];if(v==null)continue;if(v<lo)lo=v;if(v>hi)hi=v}return lo===Infinity?[0,1]:[lo,hi]}
 panes.forEach(p=>{ctx.strokeStyle=css('--grid');ctx.lineWidth=1;for(let g=0;g<=4;g++){const y=p.y+p.h*g/4;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(R,y);ctx.stroke()}});
 // price (scale on visible-only data unless reveal)
 let [plo,phi]=ext(D.close,lim);const pad=(phi-plo)*0.08;plo-=pad;phi+=pad;
  const P=panes[0];
 // pressure area
 ctx.fillStyle=css('--pres')+'55';for(let i=a;i<=lim;i++){const v=D.pressure[i];if(!v)continue;const h=v/100*P.h*0.3;ctx.fillRect(X(i),P.y+P.h-h,Math.max(1,(R-L)/(b-a)),h)}
 line(D.close,P,css('--price'),plo,phi,null,Math.min(b,cur));
 if(reveal&&cur<b)line(D.close,P,css('--future'),plo,phi,[4,3],b,cur);
 ctx.fillStyle=css('--muted');ctx.fillText((+phi).toPrecision(5),R+4,P.y+10);ctx.fillText((+plo).toPrecision(5),R+4,P.y+P.h-2);
 const P2=panes[1];const [olo,ohi]=ext(D.oi_chg,lim);const om=Math.max(Math.abs(olo),Math.abs(ohi),1);
 line(D.oi_chg,P2,css('--oi'),-om,om,null,lim);ctx.fillStyle=css('--muted');ctx.fillText('OI Δ%',R+4,P2.y+10);
 const zt=D.thresholds.cvd_z,ot=D.thresholds.oi_min_chg*100;
 ctx.strokeStyle=css('--oi')+'88';ctx.setLineDash([2,3]);let y=P2.y+P2.h-(ot+om)/(2*om)*P2.h;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(R,y);ctx.stroke();ctx.setLineDash([]);
 const P3=panes[2];line(D.fut_cvd_z,P3,css('--fut'),-6,12,null,lim);line(D.spot_cvd_z,P3,css('--spot'),-6,12,null,lim);
 ctx.strokeStyle=css('--muted');ctx.setLineDash([2,3]);y=P3.y+P3.h-(zt+6)/18*P3.h;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(R,y);ctx.stroke();ctx.setLineDash([]);
 ctx.fillStyle=css('--muted');ctx.fillText('CVD z',R+4,P3.y+10);
 // alerts
 D.alerts.forEach((ai,j)=>{if(ai<a||ai>lim)return;const x=X(ai);ctx.strokeStyle=css('--alert');ctx.lineWidth=1.2;ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,H);ctx.stroke();
  ctx.fillStyle=css('--alert');ctx.beginPath();ctx.moveTo(x,P.y+4);ctx.lineTo(x-5,P.y-4+12);ctx.lineTo(x+5,P.y-4+12);ctx.fill();
  if(reveal){ctx.fillText(D.alert_kind[j]||'',x+3,P.y+24)}});
 if(reveal)D.episodes.forEach(e=>{[['br','breakout'],['peak','peak']].forEach(([k2,lab])=>{const i=e[k2];if(i<a||i>b)return;const x=X(i);ctx.strokeStyle=css('--ink');ctx.setLineDash([5,4]);ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,P.y+P.h);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=css('--ink');ctx.fillText(lab+(k2==='peak'?' +'+e.gain+'%':''),x+3,P.y+P.h-4)})});
 // cursor
 if(cur>=a&&cur<=b){const x=X(cur);ctx.strokeStyle=css('--ink');ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,H);ctx.stroke()}
 if(!reveal&&cur<b){ctx.fillStyle=css('--grid')+'aa';ctx.fillRect(X(cur)+1,0,R-X(cur),H);ctx.fillStyle=css('--muted');ctx.fillText('อนาคต — ซ่อน',X(cur)+8,H/2)}
 ctx.fillStyle=css('--muted');ctx.fillText(fmtT(a).slice(5,16),L,H-2);const tr=fmtT(b).slice(5,16);ctx.fillText(tr,R-ctx.measureText(tr).width,H-2);
 if(drag){ctx.fillStyle=css('--price')+'22';ctx.fillRect(Math.min(drag.x0,drag.x1),0,Math.abs(drag.x1-drag.x0),H)}
 cv._X=X;cv._inv=x=>Math.round(a+(x-L)/(R-L)*(b-a));
 panel();
}
function panel(){
 const f=(v,s)=>v==null?'ไม่มีข้อมูล':(s==='%'?(v>=0?'+':'')+v.toFixed(1)+'%':s==='z'?(v>=0?'+':'')+v.toFixed(1)+'σ':v.toFixed(1)+'x');
 const rows=[['OI Expansion','oi_expansion',D.oi_chg[cur],'%','≥ +'+(D.thresholds.oi_min_chg*100)+'%'],
  ['Futures CVD','fut_cvd',D.fut_cvd_z[cur],'z','≥ '+D.thresholds.cvd_z+'σ'],['Spot CVD','spot_cvd',D.spot_cvd_z[cur],'z','≥ '+D.thresholds.cvd_z+'σ'],
  ['Relative Volume','rel_volume',D.rvol[cur],'x','≥ '+D.thresholds.rv_min+'x'],['Price Extension','not_extended',D.ret_ext[cur],'%','< +'+(D.thresholds.ext_max*100)+'%']];
 document.getElementById('ev').innerHTML='<tr><th>เงื่อนไข</th><th>ผล</th><th class="num">ค่า</th><th class="num">เกณฑ์</th></tr>'+rows.map(r=>{const ok=D.comp[r[1]][cur];return`<tr><td>${r[0]}</td><td class="${ok?'pass':'fail'}">${ok?'ผ่าน':'ไม่ผ่าน'}</td><td class="num">${f(r[2],r[3])}</td><td class="num">${r[4]}</td></tr>`}).join('');
 const p=D.pressure[cur]||0;document.getElementById('verdictNow').textContent='Signal pressure '+p+'/100 (OI · CVD · Volume ผ่านกี่ข้อ)'+(D.alerts.includes(cur)?' · ALERT เกิดที่นาทีนี้':'');
 document.getElementById('clk').textContent=fmtT(cur);document.getElementById('clk2').textContent=fmtT(cur).slice(5);
 const seen=D.alerts.map((x,j)=>[x,j]).filter(([x])=>x<=cur);
 document.getElementById('al').innerHTML=seen.length?seen.reverse().map(([x,j])=>`<tr><td>${fmtT(x).slice(5)}</td><td>${reveal?'<span class="tag">'+D.alert_kind[j]+'</span>':'<span class="tag">?</span>'}</td><td><button data-i="${x}">ดู</button></td></tr>`).join(''):'<tr><td class="note">ยังไม่มี alert ณ เวลานี้</td></tr>';
 document.querySelectorAll('#al button').forEach(bn=>bn.onclick=()=>{cur=+bn.dataset.i;sl.value=cur;view=null;draw()});
 sl.value=cur;
}
function go(i){cur=Math.max(+sl.min,Math.min(D.n-1,i));if(view&&(cur<view[0]||cur>view[1]))view=null;draw()}
sl.oninput=()=>go(+sl.value);
document.getElementById('back').onclick=()=>go(cur-60);document.getElementById('fwd').onclick=()=>go(cur+60);
document.getElementById('jump').onclick=()=>{if(D.alerts.length){view=null;go(D.alerts.find(x=>x>=+sl.min)??D.alerts[0])}};
document.getElementById('reveal').onclick=e=>{reveal=!reveal;e.target.textContent='เฉลย: '+(reveal?'เปิด':'ปิด');e.target.classList.toggle('on',reveal);draw()};
document.getElementById('play').onclick=e=>{if(timer){clearInterval(timer);timer=null;e.target.textContent='▶ เล่น';return}e.target.textContent='⏸ หยุด';timer=setInterval(()=>{if(cur>=D.n-1){clearInterval(timer);timer=null;e.target.textContent='▶ เล่น';return}go(cur+5)},60)};
cv.onmousedown=e=>{drag={x0:e.offsetX,x1:e.offsetX}};cv.onmousemove=e=>{if(drag){drag.x1=e.offsetX;draw()}};
cv.onmouseup=e=>{if(drag&&Math.abs(drag.x1-drag.x0)>8){const i0=cv._inv(Math.min(drag.x0,drag.x1)),i1=cv._inv(Math.max(drag.x0,drag.x1));view=[Math.max(0,i0),Math.min(D.n-1,i1)]}else if(drag){go(cv._inv(e.offsetX))}drag=null;draw()};
cv.ondblclick=()=>{view=null;draw()};addEventListener('resize',draw);
addEventListener('keydown',e=>{if(e.key==='ArrowRight')go(cur+(e.shiftKey?60:1));if(e.key==='ArrowLeft')go(cur-(e.shiftKey?60:1))});
// static tables
const qa=[['Q1 สัญญาณแรกเกิดเมื่อไร',A.Q1_first_signal||'ไม่มีสัญญาณก่อน breakout'],['Q2 นำหน้า breakout',A.Q2_lead_min!=null?Math.round(A.Q2_lead_min)+' นาที':'-'],
 ['Q3 False signal ก่อนหน้า',(A.Q3_false_before??'-')+' ครั้ง ใน '+(A.Q3_days_observed??'-')+' วัน'],
 ['Q4 เข้าเทรดตามสัญญาณแรก',t4?`เข้า ${(+t4.entry).toPrecision(5)} @ ${t4.entry_time} → ออก (${t4.reason}) ${(+t4.exit).toPrecision(5)} · สุทธิ ${t4.net_pct.toFixed(2)}% · MFE ${t4.mfe_pct.toFixed(1)}% · MAE ${t4.mae_pct.toFixed(1)}%`:'-'],
 ['การพุ่งหลัก',A.main_episode?`low ${A.main_episode.low_time} → breakout ${A.main_episode.breakout_time} → peak ${A.main_episode.peak_time} (+${A.main_episode.gain_pct}%)`:'ไม่พบในช่วงโฟกัส'],
 ['Baseline สุ่มเข้าเทรด',A.random_baseline&&A.random_baseline.sims?`median ${A.random_baseline.mean_net_pct_p50.toFixed(2)}% (5–95%: ${A.random_baseline.p05.toFixed(2)} … ${A.random_baseline.p95.toFixed(2)})`:'-'],
 ['ข้อมูลที่มี',Object.entries(A.data_coverage).map(([k2,v])=>k2+(v?' ✅':' ❌')).join(' · ')]];
document.getElementById('qa').innerHTML=qa.map(r=>`<tr><td style="width:240px">${r[0]}</td><td>${r[1]}</td></tr>`).join('');
document.getElementById('fm').innerHTML='<tr><th>#</th><th>Stream</th><th>ติดครั้งแรก</th><th class="num">นำหน้า (นาที)</th><th class="num">อัตราติดปกติ/วัน</th></tr>'+(A.first_mover||[]).map((r,i)=>`<tr><td>${i+1}</td><td>${r.component}</td><td>${r.first_fire}</td><td class="num">${r.lead_min??'-'}</td><td class="num">${r.baseline_fires_per_day??'-'}</td></tr>`).join('');
document.getElementById('th').textContent=JSON.stringify(D.thresholds,null,1);
if(D.backtest){document.getElementById('btcard').style.display='';const B=D.backtest;document.getElementById('bt').innerHTML='<pre>'+JSON.stringify(B,null,1)+'</pre>'}
draw();
</script></body></html>
"""
