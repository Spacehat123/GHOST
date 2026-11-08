document.querySelectorAll(".uvq15-shr").forEach(el => {
  const k=parseFloat(getComputedStyle(el).getPropertyValue("--k"))||1,rm=matchMedia("(prefers-reduced-motion:reduce)").matches;const mn=el.querySelector(".mn"),its=[...el.querySelectorAll(".it")],tt=el.querySelector(".tt");let tm=0;
const set=o=>{el.classList.toggle("open",o);mn.setAttribute("aria-expanded",String(o));its.forEach(b=>b.tabIndex=o?0:-1)};
mn.addEventListener("click",()=>set(!el.classList.contains("open")));
its.forEach((b,i)=>{b.addEventListener("click",()=>{if(i===0&&navigator.clipboard)navigator.clipboard.writeText(location.href).catch(()=>{});tt.textContent=b.dataset.m;tt.classList.add("on");clearTimeout(tm);tm=setTimeout(()=>tt.classList.remove("on"),1800/k);set(false);mn.focus()});
b.addEventListener("keydown",e=>{const d=e.key==="ArrowRight"||e.key==="ArrowDown"?1:e.key==="ArrowLeft"||e.key==="ArrowUp"?-1:0;if(d){e.preventDefault();its[(i+d+its.length)%its.length].focus()}})});
el.addEventListener("keydown",e=>{if(e.key==="Escape"&&el.classList.contains("open")){set(false);mn.focus()}});
mn.addEventListener("keydown",e=>{if(e.key==="ArrowUp"&&el.classList.contains("open")){e.preventDefault();its[0].focus()}});
const out=e=>{if(!el.contains(e.target))set(false)};document.addEventListener("pointerdown",out);return()=>{clearTimeout(tm);document.removeEventListener("pointerdown",out)}
});
