/* KAVI editorial dossier v2 — motion follows task semantics, never blocks content. */
(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  let frameRequest = 0;
  function updateProgress() {
    const limit = document.documentElement.scrollHeight - window.innerHeight;
    const percent = limit > 0 ? Math.min(1, Math.max(0, window.scrollY / limit)) : 0;
    if (byId("read-progress")) byId("read-progress").style.transform = "scaleX(" + percent + ")";
    frameRequest = 0;
  }
  document.addEventListener("scroll", () => {
    if (!frameRequest) frameRequest = requestAnimationFrame(updateProgress);
  }, {passive:true});
  updateProgress();

  // A local visual demonstration, never a tool call.
  const apparatus = document.querySelector(".apparatus");
  const packet = byId("request-packet");
  let activeMotion;
  function setOperation(mode, animate = true) {
    const allowed = mode === "read";
    document.querySelectorAll(".op-btn").forEach((button) => {
      const selected = button.dataset.operation === mode;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    if (!apparatus) return;
    apparatus.classList.toggle("is-denied", !allowed);
    byId("intent-node").textContent = allowed ? "read_customer" : "delete_customer";
    byId("guard-verdict").textContent = allowed ? "AUTHORIZED" : "DENIED";
    byId("decision-word").textContent = allowed ? "ALLOWED" : "BLOCKED";
    byId("decision-detail").textContent = allowed ? "1 authorized host dispatch" : "0 denied host dispatches";
    if (activeMotion) activeMotion.cancel();
    if (packet) {
      packet.setAttribute("cx", "140");
      if (animate && !reduce.matches && packet.animate) {
        activeMotion = packet.animate([
          {transform:"translateX(0px)"},
          {transform:"translateX(" + (allowed ? 690 : 520) + "px)"}
        ],{duration:1250,easing:"cubic-bezier(.62,.03,.18,1)",fill:"forwards"});
      } else {
        packet.setAttribute("cx", String(allowed ? 830 : 660));
      }
    }
  }
  document.querySelectorAll(".op-btn").forEach((button) => {
    button.addEventListener("click", () => setOperation(button.dataset.operation));
  });
  setOperation("read", false);

  // Both tracks replay together to preserve a direct comparison.
  let replayTimer;
  const retryPanel = document.querySelector(".retry-apparatus");
  const replay = byId("replay-btn");
  if (replay && retryPanel) replay.addEventListener("click", () => {
    retryPanel.classList.remove("replaying");
    void retryPanel.offsetWidth;
    byId("replay-status").textContent = "REPLAYING TWO RETRY POLICIES";
    if (!reduce.matches) retryPanel.classList.add("replaying");
    else byId("replay-status").textContent = "COMPARISON COMPLETE";
    clearTimeout(replayTimer);
    replayTimer = setTimeout(() => {
      retryPanel.classList.remove("replaying");
      byId("replay-status").textContent = "2 COMMITS / 1 COMMIT";
    }, reduce.matches ? 0 : 2700);
  });

  const clusters = {
    control: {
      eyebrow:"DOMAIN 01 / CONTROL", title:"The right to execute.",
      body:"Task contracts, ownership, approval, least authority and idempotent dispatch turn a suggestion into a bounded operation. An agent cannot confer its own rights.",
      tags:["Task contract","State / leases","Guard","Dispatcher"],
      names:["INTENT","TASK","GUARD","HOST"], selected:[0,1,2,3]
    },
    cognition: {
      eyebrow:"DOMAIN 02 / COGNITION", title:"A decision is a proposal.",
      body:"A planner receives a bounded context packet, chooses the next action and observes results. Models can propose and explain, but cannot own final task acceptance.",
      tags:["Context pack","Model router","Plan–act–observe","Budget"],
      names:["CONTEXT","PLAN","MODEL","OBSERVE"], selected:[0,1,2]
    },
    knowledge: {
      eyebrow:"DOMAIN 03 / KNOWLEDGE", title:"Memory has an origin.",
      body:"Durable task state, episodic memory and knowledge graph claims have separate write privileges. Every retrieved fact needs permission, provenance and a valid time horizon.",
      tags:["Provenance","Episodic memory","Entity graph","Supersession"],
      names:["SOURCE","CLAIM","GRAPH","RECALL"], selected:[0,2,3]
    },
    evidence: {
      eyebrow:"DOMAIN 04 / EVIDENCE", title:"A result needs a witness.",
      body:"External receipts, semantic acceptance tests, replay-safe traces and operator interventions determine PASS. Process exit code alone cannot close the task.",
      tags:["External receipt","Verifier","Recovery","Evaluation"],
      names:["DISPATCH","RECEIPT","VERIFY","PASS"], selected:[1,2,3]
    }
  };
  const graph = byId("layer-network");
  const NS = "http://www.w3.org/2000/svg";
  function svgEl(name, attrs, textValue) {
    const el = document.createElementNS(NS, name);
    Object.entries(attrs).forEach((entry) => el.setAttribute(entry[0], String(entry[1])));
    if (textValue) el.textContent = textValue;
    return el;
  }
  function makeNetwork(domain) {
    if (!graph) return;
    graph.replaceChildren();
    const selected = clusters[domain];
    const positions = [{x:75,y:185},{x:228,y:88},{x:386,y:270},{x:540,y:168}];
    const connections = [[0,1],[1,2],[2,3],[0,2],[1,3]];
    for(const pair of connections) {
      const p=positions[pair[0]],q=positions[pair[1]];
      graph.append(svgEl("line",{x1:p.x,y1:p.y,x2:q.x,y2:q.y,class:"layer-network-edge"}));
    }
    positions.forEach((p,i) => {
      const g=svgEl("g",{});
      g.append(svgEl("rect",{x:p.x-54,y:p.y-28,width:108,height:56,rx:0,class:selected.selected.includes(i)?"layer-network-node layer-network-focus":"layer-network-node"}));
      g.append(svgEl("text",{x:p.x,y:p.y+5,"text-anchor":"middle",class:"layer-network-label"}, selected.names[i]));
      graph.append(g);
    });
  }
  function setLayer(name, doFocus=false) {
    const layer=clusters[name];
    if (!layer) return;
    document.querySelectorAll("[data-layer]").forEach((tab) => {
      const selected=tab.dataset.layer===name;
      tab.setAttribute("aria-selected",String(selected));
      tab.tabIndex=selected?0:-1;
      if (selected) byId("layer-panel").setAttribute("aria-labelledby",tab.id);
      if (selected && doFocus) tab.focus();
    });
    byId("layer-kicker").textContent=layer.eyebrow;
    byId("layer-title").textContent=layer.title;
    byId("layer-copy").textContent=layer.body;
    byId("layer-tags").replaceChildren(...layer.tags.map((label)=>{
      const tag=document.createElement("span");tag.textContent=label;return tag;
    }));
    makeNetwork(name);
  }
  const tabButtons=[...document.querySelectorAll("[data-layer]")];
  tabButtons.forEach((button,i)=>{
    button.addEventListener("click",()=>setLayer(button.dataset.layer));
    button.addEventListener("keydown",(event)=>{
      const step=event.key==="ArrowRight"||event.key==="ArrowDown"?1:event.key==="ArrowLeft"||event.key==="ArrowUp"?-1:0;
      if(step) {
        event.preventDefault();
        const index=(i+step+tabButtons.length)%tabButtons.length;
        setLayer(tabButtons[index].dataset.layer,true);
      }
      if(event.key==="Home"){event.preventDefault();setLayer(tabButtons[0].dataset.layer,true)}
      if(event.key==="End"){event.preventDefault();setLayer(tabButtons[tabButtons.length-1].dataset.layer,true)}
    });
  });
  setLayer("control");

  const introLines=[...document.querySelectorAll(".intro h1 .line > *")];
  if(!reduce.matches&&Element.prototype.animate){
    introLines.forEach((line,index)=>{
      line.animate([{transform:"translateY(106%)",opacity:.55},{transform:"translateY(0)",opacity:1}],{
        duration:1100,delay:120+index*165,easing:"cubic-bezier(.16,1,.3,1)",fill:"both"
      });
    });
  }
  reduce.addEventListener?.("change",()=>{
    if(reduce.matches){
      if(activeMotion)activeMotion.cancel();
      setOperation(document.querySelector(".op-btn.active")?.dataset.operation||"read",false);
    }
  });
})();
