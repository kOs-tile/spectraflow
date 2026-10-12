/* Scene-derived reversible motion. Native rAF instead of generic scroll-reveal presets.
   Geometry is read once per frame, GPU-friendly transform/opacity only.
   In reduced motion, the experience remains fully static and accessible. */
(() => {
  "use strict";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const sections = [
    ["intro", ".intro", "--scene-intro"],
    ["manifesto", ".manifesto", "--scene-manifesto"],
    ["interlude", ".interlude", "--scene-interlude"],
    ["paper", ".paper", "--scene-paper"]
  ].map(([name, selector, variable]) => ({name, element:document.querySelector(selector), variable}));
  const clamp=(n)=>Math.max(0,Math.min(1,n));
  let pending=0;
  function refresh() {
    pending=0;
    const height=window.innerHeight||800;
    if (reduce.matches) {
      sections.forEach(s=>s.element?.style.setProperty(s.variable, "0"));
      return;
    }
    for (const scene of sections) {
      if (!scene.element) continue;
      const box=scene.element.getBoundingClientRect();
      let p=0;
      if (scene.name==="intro") p=clamp(-box.top/Math.max(1,box.height*.8));
      else p=clamp((height*.86-box.top)/Math.max(1,height*.95));
      scene.element.style.setProperty(scene.variable,String(p));
    }
  }
  const schedule=()=>{if(!pending)pending=requestAnimationFrame(refresh)};
  document.addEventListener("scroll",schedule,{passive:true});
  window.addEventListener("resize",schedule,{passive:true});
  reduce.addEventListener?.("change",schedule);
  refresh();
})();
