/* "Best element right now": scores all five elements for the current 時辰 with the app's
   symbolic rules, shows why, the menus that fit, and how the best element moves through the day. */
(()=>{
const el=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const gen=x=>(x+4)%5; // element that generates x (木→火→土→金→水)
const pad=n=>String(n).padStart(2,'0');

function breakdown(e,hourEl){
  const parts=[];
  if(profile.preferred!=='auto'){if(e===+profile.preferred)parts.push(['ธาตุที่คุณเลือกเน้นในโปรไฟล์',5]);return parts}
  if(e===gen(dm))parts.push([`ให้กำเนิดธาตุประจำตัว (${names[e]}→${names[dm]})`,3]);
  if(e===dm)parts.push(['ธาตุเดียวกับธาตุประจำตัว',2]);
  if(e===gen(hourEl))parts.push([`ให้กำเนิดธาตุของยามนี้ (${names[e]}→${names[hourEl]})`,2]);
  return parts;
}
const total=parts=>parts.reduce((n,p)=>n+p[1],0);
function ranking(hourEl){
  const order=[gen(dm),dm,0,1,2,3,4];
  return [0,1,2,3,4].map(e=>({e,parts:breakdown(e,hourEl)})).map(r=>({...r,points:total(r.parts)}))
    .sort((a,b)=>b.points-a.points||order.indexOf(a.e)-order.indexOf(b.e));
}
// 12 時辰: slot i covers (2i-1):00 – (2i+1):00, slot 0 = 子 23:00–00:59
function slots(){
  const p=current(),date=`${p.year}-${p.month}-${p.day}`;
  return Array.from({length:12},(_,i)=>{
    const e=eight(date,`${pad(i*2)}:30`),hourEl=Math.floor(gans.indexOf(e.getTimeGan())/2);
    return {i,pillar:e.getTime(),hourEl,start:(i*2+23)%24,best:ranking(hourEl)[0].e};
  });
}
const veg=()=>el('vegetarian').checked, avoid=()=>el('avoid').value;
function fitsDiet(m){
  if(veg()&&m.vegetarian!==true)return false;
  const a=avoid();if(!a)return true;
  const re={soy:/ถั่วเหลือง|เต้าหู้|ซีอิ๊ว|เต้าเจี้ยว|soy|tofu/i,egg:/ไข่|egg/i,fish:/ปลา|น้ำปลา|fish/i,sesame:/งา|sesame/i}[a];
  return m.ingredientsKnown&&!re.test(m.name+' '+m.ingredients.join(' '));
}

function render(){
  if(!el('bestNow'))return;
  const p=current(),h=+p.hour,min=+p.minute;
  const rank=ranking(he),best=rank[0],max=Math.max(1,best.points);
  const day=slots(),cur=Math.floor(((h+1)%24)/2),slot=day[cur],next=day[(cur+1)%12];
  const nextBoundary=h%2?h+2:h+1,left=(nextBoundary*60-(h*60+min)+1440)%1440||120;
  document.documentElement.style.setProperty('--best',`var(--el${best.e})`);
  el('bestChar').textContent=chars[best.e];
  el('bestName').textContent=names[best.e];
  el('bestPoints').textContent=`${best.points} แต้ม`;
  el('bestSlot').textContent=`ยาม ${slot.pillar} · ${pad(slot.start)}:00–${pad((slot.start+1)%24)}:59 · ยามธาตุ${names[he]}`;
  el('bestNext').textContent=next.best===best.e
    ?`อีก ${Math.floor(left/60)?Math.floor(left/60)+' ชม. ':''}${left%60} นาที เข้ายามถัดไป · ธาตุ${names[best.e]}ยังดีที่สุด`
    :`อีก ${Math.floor(left/60)?Math.floor(left/60)+' ชม. ':''}${left%60} นาที เปลี่ยนเป็นธาตุ${names[next.best]} ${chars[next.best]}`;
  el('bestWhy').textContent=best.parts.length?best.parts.map(x=>`${x[0]} +${x[1]}`).join(' · '):'ไม่มีธาตุที่ได้แต้มตามกติกา';
  el('bestBars').innerHTML=rank.map(r=>`<div class="elBar${r.e===best.e?' isBest':''}"><span class="barLabel"><b>${chars[r.e]}</b> ${names[r.e]}</span><span class="barTrack"><span class="barFill" style="width:${r.points/max*100}%;background:var(--el${r.e})"></span></span><span class="barPts">${r.points}</span><span class="barWhy">${r.parts.length?esc(r.parts.map(x=>x[0]+' +'+x[1]).join(' · ')):'—'}</span></div>`).join('');

  const builtIn=food.filter(f=>f.e===best.e&&(!veg()||f.veg)&&(!avoid()||!f.all.includes(avoid())))
    .sort((a,b)=>score(b)-score(a)).slice(0,2);
  const mine=(window.ElementFavorites?.menus?.()||[]).filter(m=>m.classification.value===best.e&&fitsDiet(m)).slice(0,3);
  const mealNow=f=>h>=f.meal[0]&&h<=f.meal[1];
  el('bestMenus').innerHTML=
    (mine.length?`<p class="miniLabel">จากร้านประจำ</p>`+mine.map(m=>`<div class="pick"><strong>${esc(m.name)}</strong><span>${esc(m.shopName)} · ${m.price!==null?esc(m.price+' บาท'):'ไม่ทราบราคา'}</span></div>`).join(''):'')+
    (builtIn.length?`<p class="miniLabel">ไอเดียเมนู</p>`+builtIn.map(f=>`<div class="pick"><strong>${esc(f.name)}</strong><span>${mealNow(f)?'เข้าช่วงมื้อนี้ +1 · ':''}${score(f)} แต้ม</span></div>`).join(''):'')+
    (!mine.length&&!builtIn.length?`<p class="small">ไม่มีเมนูธาตุ${names[best.e]}ที่ผ่านตัวกรองอาหารด้านล่าง ลองผ่อนตัวกรอง หรือเพิ่มเมนูในร้านประจำ</p>`:'')+
    `<button class="textbtn" id="bestShow">ดูเมนูธาตุ${names[best.e]}ทั้งหมด ↓</button>`;
  el('bestShow').onclick=()=>{document.querySelector(`[data-filter="${best.e}"]`)?.click();el('recommendations').scrollIntoView({behavior:'smooth'})};

  el('bestTimeline').innerHTML=day.map(s=>`<div class="slot${s.i===cur?' now':''}" style="--c:var(--el${s.best})" title="ยาม ${s.pillar} · ธาตุยาม${names[s.hourEl]} · ธาตุที่ดีที่สุด: ${names[s.best]}"><span class="slotTime">${pad(s.start)}–${pad((s.start+2)%24)}</span><b>${chars[s.best]}</b><span class="slotName">${names[s.best]}</span></div>`).join('');
}
window.addEventListener('element-change',render);
window.addEventListener('favorites-change',render);
['vegetarian','avoid'].forEach(id=>el(id).addEventListener('change',render));
render();
})();
