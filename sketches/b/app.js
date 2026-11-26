import {rank,best,fitsFor,bestSplit,complexity} from '../shared/predict.js';
const $=id=>document.getElementById(id);
const probe=await (await fetch('../data/probe.json')).json();
const DUR=probe.duration, INPUT='/mnt/pool/videos/2026-11-25 22-05-57.mp4';
const LAD=[19.4,26.0,28.0,29.8,31.2];
const S={parts:[{s:0,e:26.5},{s:26.5,e:51}],sel:0,t:0,limit:4194304,audio:false,bar:30,N:20,fitsCurve:[]};
const vid=$('vid'),cv=$('cv'),ctx=cv.getContext('2d');
const r1=x=>Math.round(x*10)/10, clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const tier=db=>db==null?'r':db>=29.5?'g':db>=27.5?'a':'r';
const COL={g:'#5ac477',a:'#e3b341',r:'#e5675b'};
const tierWord=db=>db==null?'cannot fit':db<26?'very rough':db<28?'rough':db<30?'ok':'good';
function stat(p){const r=best(probe,p.s,p.e,S.limit,S.audio);return r?{db:r.db,label:r.label,kbps:r.kbps}:{db:null,label:'-',kbps:null};}
const maxCost=Math.max(...probe.timeline)*1.05;
const M={l:46,r:50,t:22,b:24};
let W=0,H=330;
const X=t=>M.l+t/DUR*(W-M.l-M.r), T=x=>clamp((x-M.l)/(W-M.l-M.r)*DUR,0,DUR);
const DB0=18,DB1=36, Yc=v=>H-M.b-v/maxCost*(H-M.t-M.b), Yd=d=>H-M.b-(clamp(d,DB0,DB1)-DB0)/(DB1-DB0)*(H-M.t-M.b);
function size(){const d=devicePixelRatio||1;W=cv.clientWidth;cv.width=W*d;cv.height=H*d;ctx.setTransform(d,0,0,d,0,0);}
function calcFits(){S.fitsCurve=[];for(let t=0;t+S.N<=DUR+1e-6;t+=1){const r=best(probe,t,t+S.N,S.limit,S.audio);S.fitsCurve.push([t,r?r.db:null]);}}
function draw(){
 ctx.clearRect(0,0,W,H);
 const pw=W-M.l-M.r;
 // grid
 ctx.font='10px system-ui';ctx.textBaseline='middle';
 for(let d=20;d<=36;d+=2){const y=Yd(d);ctx.strokeStyle='#22262d';ctx.beginPath();ctx.moveTo(M.l,y);ctx.lineTo(W-M.r,y);ctx.stroke();ctx.fillStyle='#8d96a5';ctx.textAlign='left';ctx.fillText(d+' dB',W-M.r+4,y);}
 ctx.textAlign='center';for(let t=0;t<=DUR;t+=10){ctx.fillStyle='#8d96a5';ctx.fillText(t+'s',X(t),H-M.b+12);ctx.strokeStyle='#1c2026';ctx.beginPath();ctx.moveTo(X(t),M.t);ctx.lineTo(X(t),H-M.b);ctx.stroke();}
 ctx.textAlign='right';ctx.fillStyle='#8d96a5';ctx.fillText('cost',M.l-4,M.t-8);
 // cost area
 ctx.fillStyle='#2c4560';ctx.strokeStyle='#4a6f99';ctx.beginPath();ctx.moveTo(X(0),Yc(0));
 probe.timeline.forEach((v,i)=>{ctx.lineTo(X(i),Yc(v));ctx.lineTo(X(Math.min(i+1,DUR)),Yc(v));});
 ctx.lineTo(X(DUR),Yc(0));ctx.closePath();ctx.fill();ctx.stroke();
 // bar line
 ctx.setLineDash([4,4]);ctx.strokeStyle='#6a7280';ctx.beginPath();ctx.moveTo(M.l,Yd(S.bar));ctx.lineTo(W-M.r,Yd(S.bar));ctx.stroke();ctx.setLineDash([]);
 ctx.fillStyle='#6a7280';ctx.textAlign='left';ctx.fillText('bar',M.l+3,Yd(S.bar)-6);
 // fits curve
 ctx.strokeStyle='#c77dff';ctx.lineWidth=1.5;ctx.beginPath();let pen=false;
 for(const [t,d] of S.fitsCurve){if(d==null){pen=false;continue;}const x=X(t),y=Yd(d);pen?ctx.lineTo(x,y):ctx.moveTo(x,y);pen=true;}ctx.stroke();ctx.lineWidth=1;
 // bands
 S.parts.forEach((p,i)=>{
  const st=stat(p),c=COL[tier(st.db)],x0=X(p.s),x1=X(p.e),sel=i===S.sel;
  ctx.globalAlpha=sel?.22:.13;ctx.fillStyle=c;ctx.fillRect(x0,M.t,x1-x0,H-M.t-M.b);ctx.globalAlpha=1;
  if(tier(st.db)==='r'){ctx.save();ctx.beginPath();ctx.rect(x0,M.t,x1-x0,H-M.t-M.b);ctx.clip();ctx.strokeStyle='rgba(229,103,91,.25)';for(let k=-H;k<x1-x0;k+=10){ctx.beginPath();ctx.moveTo(x0+k,H-M.b);ctx.lineTo(x0+k+H,M.t);ctx.stroke();}ctx.restore();}
  ctx.strokeStyle=c;ctx.lineWidth=sel?2.5:1.5;ctx.strokeRect(x0,M.t,x1-x0,H-M.t-M.b);ctx.lineWidth=1;
  if(st.db!=null){const y=Yd(st.db);ctx.strokeStyle='#fff';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x1,y);ctx.stroke();ctx.lineWidth=1;}
  ctx.fillStyle=c;ctx.textAlign='left';ctx.font='bold 12px system-ui';
  const y=st.db==null?M.t+30:clamp(Yd(st.db)-10,M.t+26,H-M.b-8);
  const txt=(st.db==null?'no fit':st.db.toFixed(1)+' dB');
  ctx.fillText(txt,x0+5,y);ctx.font='10px system-ui';ctx.fillText(st.label+(st.db!=null&&st.db<28?' ROUGH':''),x0+5,y+13);
  ctx.fillStyle='#fff';ctx.font='bold 11px system-ui';ctx.fillText('#'+(i+1)+'  '+r1(p.e-p.s)+'s',x0+5,M.t+11);ctx.font='10px system-ui';
  // handles
  for(const x of [x0,x1]){ctx.fillStyle=c;ctx.fillRect(x-3,M.t+H*.2,6,H*.35);}
 });
 // playhead
 ctx.strokeStyle='#fff';ctx.beginPath();ctx.moveTo(X(S.t),M.t-6);ctx.lineTo(X(S.t),H-M.b);ctx.stroke();
 ctx.fillStyle='#fff';ctx.beginPath();ctx.moveTo(X(S.t)-5,M.t-8);ctx.lineTo(X(S.t)+5,M.t-8);ctx.lineTo(X(S.t),M.t-1);ctx.fill();
}
// ---- interaction
let drag=null,hoverT=0;
function hit(mx){
 for(let i=S.parts.length-1;i>=0;i--){const p=S.parts[i];
  if(Math.abs(mx-X(p.s))<=6)return{i,k:'s'};if(Math.abs(mx-X(p.e))<=6)return{i,k:'e'};}
 for(let i=S.parts.length-1;i>=0;i--){const p=S.parts[i];if(mx>X(p.s)&&mx<X(p.e))return{i,k:'m'};}
 return null;}
const mxOf=e=>e.clientX-cv.getBoundingClientRect().left;
cv.addEventListener('mousedown',e=>{const mx=mxOf(e),h=hit(mx);
 if(h){S.sel=h.i;drag={...h,off:T(mx)-S.parts[h.i].s,len:S.parts[h.i].e-S.parts[h.i].s,moved:false};}
 else{drag={k:'seek'};seek(T(mx));}
 sync(true);});
addEventListener('mousemove',e=>{
 const mx=mxOf(e),inside=e.target===cv;
 if(drag){const t=T(mx),p=S.parts[drag.i];
  if(drag.k==='s'){p.s=clamp(r1(t),0,p.e-0.5);seek(p.s,true);}
  else if(drag.k==='e'){p.e=clamp(r1(t),p.s+0.5,DUR);seek(p.e,true);}
  else if(drag.k==='m'){const s=clamp(r1(t-drag.off),0,DUR-drag.len);p.s=s;p.e=r1(s+drag.len);seek(p.s,true);}
  else if(drag.k==='seek')seek(t);
  sync();return;}
 if(inside){const h=hit(mx);cv.style.cursor=h?(h.k==='m'?'grab':'ew-resize'):'crosshair';
  if($('hov').checked&&!h&&vid.paused){hoverT=T(mx);hoverSeek();}}
});
addEventListener('mouseup',()=>{if(drag){drag=null;sync(true);}});
let hs=0;function hoverSeek(){const n=performance.now();if(n-hs>90){hs=n;seek(hoverT);}}
function seek(t,quiet){t=clamp(t,0,DUR-0.05);S.t=t;vid.currentTime=t;}
vid.addEventListener('timeupdate',()=>{if(!vid.paused){S.t=vid.currentTime;sync();}});
vid.addEventListener('seeked',()=>{S.t=vid.currentTime;sync();});
// ---- table
const tb=$('tbl').tBodies[0];let lastShape='';
function rowsHTML(){return S.parts.map((p,i)=>`<tr data-i="${i}" class="${i===S.sel?'sel':''}">
<td>${i+1}</td><td><input type="number" step="0.1" data-f="s" value="${p.s}"></td><td><input type="number" step="0.1" data-f="e" value="${p.e}"></td>
<td data-c="dur"></td><td data-c="db"></td><td data-c="lab"></td><td data-c="kb"></td><td><button data-a="del" ${S.parts.length<2?'disabled':''}>x</button></td></tr>`).join('');}
tb.addEventListener('change',e=>{const tr=e.target.closest('tr');if(!tr||!e.target.dataset.f)return;const p=S.parts[+tr.dataset.i],v=r1(+e.target.value);
 if(e.target.dataset.f==='s')p.s=clamp(v,0,p.e-0.5);else p.e=clamp(v,p.s+0.5,DUR);S.sel=+tr.dataset.i;seek(e.target.dataset.f==='s'?p.s:p.e);sync(true);});
tb.addEventListener('click',e=>{const tr=e.target.closest('tr');if(!tr)return;const i=+tr.dataset.i;
 if(e.target.dataset.a==='del'){S.parts.splice(i,1);S.sel=Math.min(S.sel,S.parts.length-1);}
 else if(e.target.tagName!=='INPUT')S.sel=i;sync(true);});
function updTable(rebuild){
 const shape=S.parts.length+':'+S.sel;
 if(rebuild||shape!==lastShape){tb.innerHTML=rowsHTML();lastShape=shape;}
 [...tb.rows].forEach((tr,i)=>{const p=S.parts[i],st=stat(p);
  for(const f of ['s','e']){const inp=tr.querySelector(`[data-f=${f}]`);if(document.activeElement!==inp)inp.value=p[f];}
  tr.querySelector('[data-c=dur]').textContent=r1(p.e-p.s)+' s';
  tr.querySelector('[data-c=db]').innerHTML=st.db==null?'<span class="tag t-r">no fit</span>':`<span class="db">${st.db.toFixed(1)}</span> <span class="tag t-${tier(st.db)}">${tierWord(st.db)}</span>`;
  tr.querySelector('[data-c=lab]').textContent=st.label;
  tr.querySelector('[data-c=kb]').textContent=st.kbps==null?'-':Math.round(st.kbps);});
 const tot=S.parts.reduce((a,p)=>a+p.e-p.s,0);$('tot').textContent=`total ${r1(tot)} s in ${S.parts.length} part${S.parts.length>1?'s':''}`;
}
// ---- actions
$('addPart').onclick=()=>{const s=r1(clamp(S.t,0,DUR-1));S.parts.push({s,e:r1(Math.min(DUR,s+10))});S.sel=S.parts.length-1;sync(true);};
$('mkStart').onclick=()=>{const p=S.parts[S.sel];p.s=clamp(r1(S.t),0,p.e-0.5);sync(true);};
$('mkEnd').onclick=()=>{const p=S.parts[S.sel];p.e=clamp(r1(S.t),p.s+0.5,DUR);sync(true);};
function split(n){const p=S.parts[S.sel],t0=performance.now();const r=bestSplit(probe,p.s,p.e,S.limit,n,S.audio);
 if(!r){$('spmsg').textContent='no valid split (some part cannot fit)';return;}
 const edges=[p.s,...r.cuts.map(r1),p.e],np=[];for(let i=0;i<edges.length-1;i++)np.push({s:edges[i],e:edges[i+1]});
 S.parts.splice(S.sel,1,...np);$('spmsg').textContent=`worst part ${r.worst.toFixed(1)} dB, cuts at ${r.cuts.map(c=>r1(c)+'s').join(', ')} (${Math.round(performance.now()-t0)} ms)`;sync(true);}
$('sp2').onclick=()=>split(2);$('sp3').onclick=()=>split(3);
for(const [id,f] of [['lim',v=>S.limit=Math.max(1e5,+v||4194304)],['bar',v=>S.bar=+v||30],['nlen',v=>S.N=clamp(+v||20,2,DUR)]])$(id).oninput=e=>{f(e.target.value);calcFits();sync();};
$('aud').onchange=e=>{S.audio=e.target.checked;calcFits();sync(true);};
$('ladsel').onchange=()=>{const v=$('ladsel').value;$('lad').src=v==='context'?'../data/ladder/context.png':`../data/ladder/quality-bar-${v}.png`;sync();};
// ---- fits / command / ladder
function updFits(){const d=fitsFor(probe,S.t,S.limit,S.bar,S.audio);
 $('fitout').textContent=d?`${d.toFixed(1)} s  (${S.t.toFixed(1)} to ${(S.t+d).toFixed(1)} s)`:`nothing fits at ${S.bar} dB from here`;}
function updCmd(){const base=`fit4 '${INPUT}'`;const lines=S.parts.map((p,i)=>`${base} --start ${p.s} --end ${p.e}${S.audio?'':' --no-audio'} -o part${i+1}.webm`);
 $('cmd').textContent=(S.limit!==4194304?`# size limit ${S.limit} bytes\n`:'')+lines.join('\n');}
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText($('cmd').textContent);$('copy').textContent='copied';}catch{const r=document.createRange();r.selectNodeContents($('cmd'));getSelection().removeAllRanges();getSelection().addRange(r);$('copy').textContent='selected - press ctrl+c';}setTimeout(()=>$('copy').textContent='copy',1500);};
function updLad(){const p=S.parts[S.sel],st=stat(p),v=$('ladsel').value;
 $('lcols').style.display=v==='context'?'none':'block';
 let hi='';if(st.db!=null){let m=1e9;LAD.forEach(x=>{if(Math.abs(x-st.db)<m){m=Math.abs(x-st.db);hi=x;}});}
 $('lcols').innerHTML=st.db==null?'':`<b style="color:var(--ac)">Part ${S.sel+1} predicts ${st.db.toFixed(1)} dB: closest panel is ${hi} dB</b> (panels, left to right, top to bottom: source, 19.4, 26.0, 28.0, 29.8, 31.2)`;
 $('ladnote').textContent=v==='context'?'full frame with crop boxes':'~26 smeared, ~28 texture mostly gone, ~30 readable, 31+ near source';}
function sync(rebuild){$('tnow').textContent=S.t.toFixed(1)+' s';updTable(rebuild);updFits();updCmd();updLad();draw();}
new ResizeObserver(()=>{size();draw();}).observe(cv);
size();calcFits();sync(true);
