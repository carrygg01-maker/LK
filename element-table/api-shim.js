/* Local stand-in for the original site's /api/* backend (ChatGPT Sites).
   Stores favorite shops in localStorage. It does NOT crawl 7-Eleven or any other site:
   "refresh" just re-stamps the check time. Add menus via the manual-menu box in the shop dialog. */
(()=>{
const KEY='elementShops.v1';
const now=()=>new Date().toISOString();
const seed=()=>{const t=now(),u='https://www.7eleven.co.th/new-product/bakery/';
 const m=(name,element,ingredients,path)=>({name,price:null,currency:'THB',ingredients,ingredientsKnown:false,element,role:'main',vegetarian:null,origin:'source',sourceUrl:u+path,observedAt:t});
 return [{id:'54593ebd-ef40-42ff-9e47-04313a754207',name:'7-11',area:'',sourceUrl:'',auto:true,status:'ok',researchState:'done',
  lastSuccess:t,lastChecked:t,changes:{added:0},updatedAt:t,archived:false,manualMenus:[],
  researchNote:'ระบบค้นแค็ตตาล็อกและรายละเอียดสินค้าจากเว็บ 7-Eleven ทางการ มีแหล่งอ้างอิงต่อสินค้า วัตถุดิบจากคำอธิบายยังไม่ใช่ฉลากครบถ้วน ราคาและสต็อกสาขาต้องตรวจอีกครั้ง',
  sourceMenus:[
   m('แซนวิชครัวซองค์ เบคอนไข่',1,['เนย','มะเขือเทศ'],'474-แซนวิชครัวซองค์-เบคอนไข่'),
   m('กล้วยไข่มะพร้าวอ่อน',2,['นมสด','กะทิ','มะพร้าว'],'248-กล้วยไข่มะพร้าวอ่อน'),
   m('เบอร์เกอร์โฟร์ชีส ผักโขมและแฮม',0,['ผักโขม'],'475-เบอร์เกอร์โฟร์ชีส-ผักโขมและแฮม-'),
   m('ปีกไก่ไข่พะโล้',2,[],'241-ปีกไก่ไข่พะโล้')]}]};
const load=()=>{try{const v=JSON.parse(localStorage.getItem(KEY));if(Array.isArray(v))return v}catch{}const s=seed();save(s);return s};
const save=v=>{try{localStorage.setItem(KEY,JSON.stringify(v))}catch{}};
const json=(b,status=200)=>new Response(JSON.stringify(b),{status,headers:{'Content-Type':'application/json'}});
const real=window.fetch.bind(window);
window.fetch=async(url,opt={})=>{
 const u=String(url); if(!u.startsWith('/api/'))return real(url,opt);
 const path=u.slice(5), data=opt.body?JSON.parse(opt.body):null; let all=load();
 if(path==='restaurants'&&!data)return json({restaurants:all.filter(s=>!s.archived)});
 if(path==='restaurants'){
  const name=String(data.name||'').trim(); if(!name)return json({error:'กรุณาใส่ชื่อร้าน'},400);
  let s=data.id&&all.find(x=>x.id===data.id);
  if(!s){s={id:crypto.randomUUID(),sourceMenus:[],lastSuccess:null,lastChecked:null,status:'ok',changes:null};all.push(s)}
  Object.assign(s,{name,area:data.area||'',sourceUrl:data.sourceUrl||'',auto:!!data.auto,
   manualMenus:(data.manualMenus||[]).map(m=>({...m,origin:'manual',ingredientsKnown:m.ingredients.length>0})),updatedAt:now(),archived:false});
  save(all);return json({restaurant:s});}
 if(path==='archive'){const s=all.find(x=>x.id===data.id);if(s)s.archived=true;save(all);return json({ok:true});}
 if(path==='refresh'){const list=all.filter(s=>!s.archived&&(!data.id||s.id===data.id));
  list.forEach(s=>{s.lastChecked=now()});save(all);
  return json(data.id?{restaurant:list[0]}:{results:list});}
 return json({error:'not found'},404);};
})();
