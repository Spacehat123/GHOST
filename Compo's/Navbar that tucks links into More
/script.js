document.querySelectorAll(".uvq12-pp").forEach(el => {
  const nav=el.querySelector("nav"),ls=el.querySelector(".ls"),it=[...ls.children],mr=el.querySelector(".mr"),bt=mr.querySelector("button"),ct=bt.querySelector("b"),mi=[...el.querySelectorAll(".mm li")],hd=el.querySelector(".hd");let k0=-1;
const fit=()=>{it.forEach(li=>li.hidden=false);mr.hidden=true;let k=it.length;if(it[k-1].offsetLeft+it[k-1].offsetWidth>ls.clientWidth+1){mr.hidden=false;const av=ls.clientWidth;k=it.filter(li=>li.offsetLeft+li.offsetWidth<=av+1).length}it.forEach((li,j)=>li.hidden=j>=k);mi.forEach((li,j)=>li.hidden=j<k);ct.textContent=it.length-k;if(k!==k0&&k0>-1){ct.classList.remove("bump");void ct.offsetWidth;ct.classList.add("bump")}k0=k;if(mr.hidden)set(false);hc()};
const hc=()=>mr.classList.toggle("hc",mi.some(li=>!li.hidden&&li.querySelector("[aria-current]")));
const set=o=>{mr.classList.toggle("open",o);bt.setAttribute("aria-expanded",String(o))};
bt.addEventListener("click",()=>{set(!mr.classList.contains("open"));if(mr.classList.contains("open")){const a=mi.find(li=>!li.hidden);if(a)a.querySelector("a").focus()}});
const all=[...it,...mi].map(li=>li.querySelector("a"));all.forEach((a,j)=>a.addEventListener("click",e=>{e.preventDefault();const n=j%it.length;all.forEach((x,m)=>{if(m%it.length===n)x.setAttribute("aria-current","page");else x.removeAttribute("aria-current")});hc();if(mr.contains(a)){set(false);bt.focus()}}));
mr.addEventListener("keydown",e=>{const v=mi.filter(li=>!li.hidden).map(li=>li.querySelector("a")),i=v.indexOf(document.activeElement);if(e.key==="Escape"){set(false);bt.focus()}if(i>-1&&(e.key==="ArrowDown"||e.key==="ArrowUp")){e.preventDefault();v[(i+(e.key==="ArrowDown"?1:-1)+v.length)%v.length].focus()}});
mr.addEventListener("focusout",e=>{if(!mr.contains(e.relatedTarget))set(false)});
let dg=null;hd.addEventListener("pointerdown",e=>{const sc=el.getBoundingClientRect().width/el.offsetWidth,em=parseFloat(getComputedStyle(el).fontSize)*sc;dg={x:e.clientX,d:parseFloat(el.style.getPropertyValue("--d"))||0,em};hd.setPointerCapture(e.pointerId)});
hd.addEventListener("pointermove",e=>{if(dg)el.style.setProperty("--d",(dg.d+(e.clientX-dg.x)/dg.em).toFixed(2)+"em")});const end=()=>{dg=null};hd.addEventListener("pointerup",end);hd.addEventListener("pointercancel",end);
const ro=new ResizeObserver(fit);ro.observe(nav);fit();return()=>ro.disconnect()
});
