/* Local stand-in for the /api/* backend when server.py is not running (e.g. the published page).
   Stores favorite shops in localStorage. It cannot fetch other websites, so shops named 7-11 / เซเว่น
   use the 7-Eleven catalog snapshot in seven-eleven-catalog.js (python3 server.py --snapshot-7eleven …).
   Other shops: add menus via the manual-menu box in the shop dialog. */
(()=>{
const KEY='elementShops.v1';
const now=()=>new Date().toISOString();
const CAT=window.SEVEN_ELEVEN_CATALOG||null, IS_711=/7\s*-?\s*(11|eleven)|เซเว่น/i;
const catDate=()=>new Date(CAT.fetchedAt).toLocaleDateString('th-TH',{timeZone:'Asia/Bangkok',dateStyle:'medium'});
// Apply the catalog to 7-11 shops that still use the source catalog. Returns the number of new menu names.
function applyCatalog(s,force){
 if(!CAT||!IS_711.test(s.name)||s.sourceUrl&&!/7eleven\.co\.th/.test(s.sourceUrl))return null;
 if(!force&&s.catalogAt===CAT.fetchedAt)return 0;
 const old=new Set((s.sourceMenus||[]).map(m=>m.name)),menus=CAT.menus.map(m=>({...m}));
 const added=menus.filter(m=>!old.has(m.name)).length;
 Object.assign(s,{sourceMenus:menus,catalogAt:CAT.fetchedAt,lastSuccess:CAT.fetchedAt,status:'ok',researchState:'done',changes:{added},error:null,
  researchNote:`แค็ตตาล็อกสินค้าใหม่จากเว็บ 7-Eleven ทางการ ${menus.length} รายการ (ดึงเมื่อ ${catDate()}) • ราคาและวัตถุดิบจากคำอธิบายบนเว็บ ไม่ใช่ฉลากครบถ้วน • สต็อกสาขาต้องตรวจอีกครั้ง`});
 return added;
}
const seed=()=>{const t=now(),s={id:'54593ebd-ef40-42ff-9e47-04313a754207',name:'7-11',area:'',sourceUrl:'',auto:true,status:'ok',researchState:'done',
  lastSuccess:null,lastChecked:t,changes:{added:0},updatedAt:t,archived:false,manualMenus:[],sourceMenus:[]};applyCatalog(s,true);return [s]};
const load=()=>{try{const v=JSON.parse(localStorage.getItem(KEY));if(Array.isArray(v)){let changed=false;v.forEach(s=>{if(!s.archived&&applyCatalog(s,false))changed=true});if(changed)save(v);return v}}catch{}const s=seed();save(s);return s};
const save=v=>{try{localStorage.setItem(KEY,JSON.stringify(v))}catch{}};
const json=(b,status=200)=>new Response(JSON.stringify(b),{status,headers:{'Content-Type':'application/json'}});
const real=window.fetch.bind(window);
// Use the real backend (server.py) when it answers /api/health; otherwise keep everything in this browser.
let mode=null;const detect=async()=>{if(mode)return mode;try{const r=await real('/api/health',{signal:AbortSignal.timeout(4000)});const j=r.ok?await r.json():{};mode=j.mode==='server'?'server':'local'}catch{mode='local'}window.ElementBackend=mode;window.dispatchEvent(new Event('backend-mode'));return mode};
window.ElementBackendReady=detect();
window.fetch=async(url,opt={})=>{
 const u=String(url); if(!u.startsWith('/api/'))return real(url,opt);
 if(await detect()==='server')return real(url,opt);
 const path=u.slice(5), data=opt.body?JSON.parse(opt.body):null; let all=load();
 if(path==='restaurants'&&!data)return json({restaurants:all.filter(s=>!s.archived)});
 if(path==='restaurants'){
  const name=String(data.name||'').trim(); if(!name)return json({error:'กรุณาใส่ชื่อร้าน'},400);
  let s=data.id&&all.find(x=>x.id===data.id);
  if(!s){s={id:crypto.randomUUID(),sourceMenus:[],lastSuccess:null,lastChecked:null,status:'ok',changes:null};all.push(s)}
  Object.assign(s,{name,area:data.area||'',sourceUrl:data.sourceUrl||'',auto:!!data.auto,
   manualMenus:(data.manualMenus||[]).map(m=>({...m,origin:'manual',ingredientsKnown:m.ingredients.length>0})),updatedAt:now(),archived:false});
  applyCatalog(s,false);
  save(all);return json({restaurant:s});}
 if(path==='archive'){const s=all.find(x=>x.id===data.id);if(s)s.archived=true;save(all);return json({ok:true});}
 if(path==='refresh'){const list=all.filter(s=>!s.archived&&(!data.id||s.id===data.id));
  let catalog=0;list.forEach(s=>{s.lastChecked=now();const n=applyCatalog(s,false);if(n!==null){catalog++;if(n===0)s.changes={added:0}}});save(all);
  const extra={mode:'local',catalogShops:catalog,catalogDate:CAT?catDate():null,catalogCount:CAT?CAT.menus.length:0};
  return json(data.id?{restaurant:list[0],...extra}:{results:list,...extra});}
 return json({error:'not found'},404);};
})();
