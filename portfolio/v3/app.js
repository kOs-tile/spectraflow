/* KAVI / AGENT AUTOPSY — reading-driven instrument logic.
   Every interactive demonstration is synthetic; actual proofs live in cited code.
   No API calls, cookies, analytics or write operations. */
(() => {
"use strict";
const q=(x)=>document.querySelector(x);
const qa=(x)=>[...document.querySelectorAll(x)];
const reduce=window.matchMedia("(prefers-reduced-motion: reduce)");
const clamp=(x)=>Math.max(0,Math.min(1,x));
const ease=(x)=>x*x*(3-2*x);
const layers=["claim","authority","dispatch","verdict"];
const labels=["THE CLAIM","THE PERMISSION","THE COMMIT","THE VERDICT"];
const captions=[
 "The model speaks. No external state has yet been proven.",
 "The host checks current authority before making any tool call.",
 "A dispatch can commit even if its acknowledgement disappears.",
 "Only external evidence can justify the terminal PASS."
];
const stage=q(".autopsy-scroll");
const sheets=layers.map(name=>q(".sheet-"+name));
const heroTitle=q("#hero-heading");
let lastStage=-1,frame=0;
function inspectScroll(){
 frame=0;
 if(!stage)return;
 const y=stage.getBoundingClientRect().top;
 const travel=Math.max(1,stage.offsetHeight-window.innerHeight);
 const total=clamp(-y/travel);
 const peeling=[
  ease(clamp((total-.09)/.21)),
  ease(clamp((total-.32)/.21)),
  ease(clamp((total-.55)/.21))
 ];
 if(!reduce.matches){
   sheets[0].style.transform="translate3d("+(-125*peeling[0])+"vw,"+(-53*peeling[0])+"vh,0) rotate("+(-17*peeling[0])+"deg)";
   sheets[1].style.transform="translate3d("+(123*peeling[1])+"vw,"+(-48*peeling[1])+"vh,0) rotate("+(19*peeling[1])+"deg)";
   sheets[2].style.transform="translate3d("+(-115*peeling[2])+"vw,"+(49*peeling[2])+"vh,0) rotate("+(-20*peeling[2])+"deg)";
   if(heroTitle)heroTitle.style.opacity=String(1-.62*clamp(total/.55));
   if(q("#stage-progress"))q("#stage-progress").style.width=(25+75*total)+"%";
 }
 const current=total<.28?0:total<.51?1:total<.75?2:3;
 if(current!==lastStage){
  lastStage=current;
  q("#stage-counter").textContent="0"+(current+1)+" / 04";
  q("#stage-label").textContent=labels[current];
  q("#stage-caption").textContent=captions[current];
 }
}
function requestScroll(){
 if(!frame)frame=requestAnimationFrame(inspectScroll);
}
window.addEventListener("scroll",requestScroll,{passive:true});
window.addEventListener("resize",requestScroll,{passive:true});
reduce.addEventListener?.("change",()=>{
 if(reduce.matches){
  sheets.forEach(s=>s.style.removeProperty("transform"));
  if(heroTitle)heroTitle.style.opacity="1";
 }
 requestScroll();
});
inspectScroll();

/* Specimen I: the switch changes our synthetic gate diagram only. */
const state={
 read:{allowed:true,count:"1",code:"PASS",detail:"The requested read matches the scoped authority capsule."},
 delete:{allowed:false,count:"0",code:"DENY",detail:"delete_customer has no grant; the host callback is never invoked."}
};
function setAction(action){
 const item=state[action];if(!item)return;
 qa("[data-action]").forEach(btn=>{
   const yes=btn.dataset.action===action;
   btn.classList.toggle("selected",yes);
   btn.setAttribute("aria-pressed",String(yes));
 });
 const box=q(".mechanism-outcome");
 box.classList.toggle("is-denied",!item.allowed);
 q("#action-verdict").textContent=item.allowed?"ALLOWED":"BLOCKED";
 q("#action-explanation").textContent=item.detail;
 q("#callback-count").textContent=item.count;
 q("#guard-state").textContent=item.code;
 const p=q("#mechanism-pulse");
 if(p){
  p.style.background=item.allowed?"#285c61":"#c65337";
  const travel=item.allowed?"100%":"51%";
  p.animate?.([{left:"0"},{left:travel}],{duration:reduce.matches?0:700,easing:"ease-in-out",fill:"forwards"});
  p.style.left=travel;
 }
}
qa("[data-action]").forEach(btn=>btn.addEventListener("click",()=>setAction(btn.dataset.action)));
setAction("read");

/* Specimen II: comparative historical-independent synthetic benchmark. */
let replayTimer;
q("#replay")?.addEventListener("click",()=>{
 const wall=q(".ledger-wall");
 wall?.classList.remove("replaying");
 void wall?.offsetWidth;
 if(!reduce.matches)wall?.classList.add("replaying");
 q("#replay-result").textContent="FAULT INJECTION / REPLAYING";
 clearTimeout(replayTimer);
 replayTimer=setTimeout(()=>{
  wall?.classList.remove("replaying");
  q("#replay-result").textContent="2 COMMITS / 1 COMMIT — REPRODUCED SYNTHETICALLY";
 },reduce.matches?0:2000);
});

/* Specimen III: only the selected memory stratum is "raised". */
const memories={
 context:{num:"01",title:"A context window is rented space.",label:"BOUNDED INFERENCE",copy:"Task-specific retrieval, current observations and constrained tool definitions are compiled just in time. The text vanishes after inference; it is not canonical state.",owner:"Context compiler"},
 state:{num:"02",title:"Task state is not a summary.",label:"DURABLE EXECUTION",copy:"The canonical task ledger records ownership, attempts, receipts and terminal acceptance. It cannot be rewritten by an LLM-generated recap.",owner:"Control-plane transaction"},
 episode:{num:"03",title:"Experience needs a timestamp.",label:"PROVENANCE-GATED HISTORY",copy:"Episodes describe what occurred, when and with which source. They can inform future tasks but cannot silently become authoritative policy.",owner:"Verified event ingestion"},
 knowledge:{num:"04",title:"An edge is still a claim.",label:"EVIDENCE-LINKED GRAPH",copy:"Entities and relations must retain source links, observation time, tenant permissions and supersession rules. Similarity alone does not establish truth.",owner:"Evidence-gated claim writer"},
 procedure:{num:"05",title:"Instructions must be versioned.",label:"TESTED PROCEDURAL MEMORY",copy:"Reusable playbooks are reviewed, versioned and tested before tools act on them. A downloaded page cannot install new operating authority.",owner:"Human-approved procedure release"}
};
const strataMap={context:0,state:1,episode:2,knowledge:3,procedure:4};
const tabs=qa("[data-memory]");
function chooseMemory(key,focus=false){
 const data=memories[key];if(!data)return;
 tabs.forEach(tab=>{
  const selected=tab.dataset.memory===key;
  tab.setAttribute("aria-selected",String(selected));
  tab.tabIndex=selected?0:-1;
  if(selected){q("#memory-panel").setAttribute("aria-labelledby",tab.id);if(focus)tab.focus();}
 });
 qa(".stratum").forEach((s,i)=>s.classList.toggle("active",i===strataMap[key]));
 q("#memory-numeric").textContent=data.num+" / 05";
 q("#stratum-marker").textContent=data.num;
 q("#memory-label").textContent=data.label;
 q("#memory-name").textContent=data.title;
 q("#memory-copy").textContent=data.copy;
 q("#memory-authority").textContent=data.owner;
}
tabs.forEach((tab,index)=>{
 tab.addEventListener("click",()=>chooseMemory(tab.dataset.memory));
 tab.addEventListener("keydown",(event)=>{
  const dir=(event.key==="ArrowRight"||event.key==="ArrowDown")?1:(event.key==="ArrowLeft"||event.key==="ArrowUp")?-1:0;
  if(dir){event.preventDefault();chooseMemory(tabs[(index+dir+tabs.length)%tabs.length].dataset.memory,true);}
  else if(event.key==="Home"){event.preventDefault();chooseMemory(tabs[0].dataset.memory,true);}
  else if(event.key==="End"){event.preventDefault();chooseMemory(tabs[tabs.length-1].dataset.memory,true);}
 });
});
chooseMemory("state");
})();
