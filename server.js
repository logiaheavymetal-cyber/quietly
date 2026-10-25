'use strict';
// Quietly: servidor intermedio entre la interfaz y SearXNG. Sin dependencias (Node 20+).
const http=require('http'),fs=require('fs'),path=require('path'),crypto=require('crypto'),dns=require('dns').promises,net=require('net');
const PORT=+process.env.PORT||3000,SEARX=(process.env.SEARX_URL||'http://localhost:8080').replace(/\/+$/,''),
 RATE=+process.env.RATE_PER_MIN||40,TRUST=process.env.TRUST_PROXY==='1',TTL=600e3,KEY=crypto.randomBytes(32);
let salt=crypto.randomBytes(16);setInterval(()=>{salt=crypto.randomBytes(16)},864e5).unref();
// Interfaz: sin fuentes de Google (filtran la IP) y apuntando a este mismo servidor.
const html=fs.readFileSync(path.join(__dirname,'public','index.html'),'utf8')
 .replace(/<link[^>]*fonts\.googleapis[^>]*>/g,'').replace("ls('q_url')||''","ls('q_url')||location.origin");
const H={'referrer-policy':'no-referrer','x-content-type-options':'nosniff','permissions-policy':'geolocation=(), camera=(), microphone=()',
 'content-security-policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'"};
const end=(r,c,b)=>{r.writeHead(c);r.end(b)};
const json=(r,c,d)=>{r.writeHead(c,{'content-type':'application/json','cache-control':'no-store'});r.end(JSON.stringify(d))};
const sign=u=>crypto.createHmac('sha256',KEY).update(u).digest('base64url').slice(0,22);
// Caché en memoria (10 min). No se escribe nada en disco.
const cache=new Map();
const getC=k=>{const v=cache.get(k);if(v&&v.t>Date.now())return v.d;cache.delete(k)};
const setC=(k,d)=>{cache.set(k,{d,t:Date.now()+TTL});if(cache.size>500)cache.delete(cache.keys().next().value)};
// Límite por minuto: la IP se convierte en un hash con sal que cambia cada día y no se guarda.
const hits=new Map();setInterval(()=>hits.clear(),6e4).unref();
function limited(req,cost){
 const ip=TRUST?(req.headers['x-forwarded-for']||'').split(',')[0].trim()||req.socket.remoteAddress:req.socket.remoteAddress;
 const id=crypto.createHash('sha256').update(salt).update(ip||'').digest('base64').slice(0,12);
 const n=(hits.get(id)||0)+cost;hits.set(id,n);return n>RATE}
function priv(ip){
 if(net.isIPv6(ip))return ip==='::1'||/^(fc|fd|fe80)/i.test(ip)||(ip.startsWith('::ffff:')&&priv(ip.slice(7)));
 const p=ip.split('.').map(Number);
 return p[0]===10||p[0]===127||p[0]===0||(p[0]===169&&p[1]===254)||(p[0]===172&&p[1]>=16&&p[1]<=31)||(p[0]===192&&p[1]===168)||(p[0]===100&&p[1]>=64&&p[1]<=127)||p[0]>=224}
async function search(u,res){
 const p=u.searchParams,q=(p.get('q')||'').trim().slice(0,200);
 if(!q)return json(res,400,{error:'q'});
 const cat=['general','images','files'].includes(p.get('categories'))?p.get('categories'):'general',
  page=Math.min(Math.max(+p.get('pageno')||1,1),10),safe=['0','1','2'].includes(p.get('safesearch'))?p.get('safesearch'):'1',
  k=[q,cat,page,safe].join('|');
 let d=getC(k);
 if(!d){
  const s=new URL('/search',SEARX);s.search=new URLSearchParams({q,format:'json',categories:cat,pageno:page,safesearch:safe});
  const r=await fetch(s,{signal:AbortSignal.timeout(8000),headers:{accept:'application/json'}});
  if(!r.ok)return json(res,502,{error:'upstream '+r.status});
  const raw=(await r.json()).results||[];
  d={results:raw.slice(0,30).map(x=>{const im=x.thumbnail_src||x.thumbnail||x.img_src;
   return{title:String(x.title||'').slice(0,200),url:x.url,content:String(x.content||'').slice(0,300),filesize:x.filesize,
    thumbnail_src:/^https?:/.test(im||'')?`/img?u=${encodeURIComponent(im)}&s=${sign(im)}`:undefined}})};
  setC(k,d)}
 json(res,200,d)}
// Proxy de miniaturas: solo URLs firmadas por este servidor, sin redes privadas, solo imágenes, máx. 3 MB.
async function img(u,res,depth=0){
 let url;try{url=new URL(u)}catch{return end(res,400)}
 if(!/^https?:$/.test(url.protocol)||depth>3)return end(res,400);
 const{address}=await dns.lookup(url.hostname);if(priv(address))return end(res,403);
 const r=await fetch(url,{redirect:'manual',signal:AbortSignal.timeout(6000),headers:{'user-agent':'Mozilla/5.0',accept:'image/*'}});
 const loc=r.headers.get('location');
 if(r.status>=300&&r.status<400&&loc)return img(new URL(loc,url).href,res,depth+1);
 const ct=r.headers.get('content-type')||'';
 if(!r.ok||!/^image\/(png|jpe?g|gif|webp|avif)/.test(ct))return end(res,404);
 if(+r.headers.get('content-length')>3e6)return end(res,413);
 const buf=Buffer.from(await r.arrayBuffer());if(buf.length>3e6)return end(res,413);
 res.writeHead(200,{'content-type':ct,'cache-control':'public, max-age=86400'});res.end(buf)}
http.createServer(async(req,res)=>{
 for(const k in H)res.setHeader(k,H[k]);
 try{
  const u=new URL(req.url,'http://x');
  if(req.method!=='GET')return end(res,405);
  if(u.pathname==='/'){res.writeHead(200,{'content-type':'text/html;charset=utf-8','cache-control':'no-cache'});return res.end(html)}
  if(u.pathname==='/health')return end(res,200,'ok');
  if(u.pathname==='/search'){if(limited(req,1))return json(res,429,{error:'rate'});return await search(u,res)}
  if(u.pathname==='/img'){
   if(limited(req,.1))return end(res,429);
   const t=u.searchParams.get('u')||'';if(u.searchParams.get('s')!==sign(t))return end(res,403);
   return await img(t,res)}
  end(res,404)
 }catch(e){if(!res.headersSent)json(res,502,{error:'fail'});else res.end()}
}).listen(PORT,()=>console.log('Quietly escuchando en el puerto '+PORT));
