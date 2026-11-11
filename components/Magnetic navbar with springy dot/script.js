document.querySelectorAll(".uvq12-ml").forEach(el => {
  const ul=el.querySelector("ul"),ls=[...el.querySelectorAll("a")],dt=el.querySelector(".dt");let cur=ls[0],tg=null,x=0,v=0,raf=0,last=0;
const rm=matchMedia("(prefers-reduced-motion: reduce)").matches;const cx=a=>a.parentElement.offsetLeft+a.parentElement.offsetWidth/2;
const loop=t=>{const dt0=Math.min(32,t-(last||t))/16.7;last=t;const g=cx(tg||cur);v+=(g-x)*.14*dt0;v*=Math.pow(.74,dt0);x+=v*dt0;const s=1+Math.min(Math.abs(v)/6,2.2);dt.style.transform="translateX("+x.toFixed(2)+"px) scaleX("+s.toFixed(3)+")";if(Math.abs(g-x)<.2&&Math.abs(v)<.05){x=g;dt.style.transform="translateX("+x+"px)";raf=0;last=0;return}raf=requestAnimationFrame(loop)};
const kick=()=>{if(rm){x=cx(tg||cur);dt.style.transform="translateX("+x+"px)";return}if(!raf)raf=requestAnimationFrame(loop)};
ls.forEach(a=>{a.addEventListener("pointerenter",()=>{tg=a;kick()});a.addEventListener("pointermove",e=>{if(rm||e.pointerType!=="mouse")return;const r=a.getBoundingClientRect();a.classList.add("mg");a.style.transform="translate("+((e.clientX-r.left-r.width/2)*.3).toFixed(1)+"px,"+((e.clientY-r.top-r.height/2)*.35).toFixed(1)+"px)"});a.addEventListener("pointerleave",()=>{a.classList.remove("mg");a.style.transform="";tg=null;kick()});
a.addEventListener("focus",()=>{tg=a;kick()});a.addEventListener("blur",()=>{tg=null;kick()});
a.addEventListener("click",e=>{e.preventDefault();cur=a;ls.forEach(o=>{if(o===a)o.setAttribute("aria-current","page");else o.removeAttribute("aria-current")});kick()});
a.addEventListener("keydown",e=>{const i=ls.indexOf(a),n=e.key==="ArrowRight"?i+1:e.key==="ArrowLeft"?i-1:-1;if(n>=0&&n<ls.length){e.preventDefault();ls[n].focus()}})});
x=cx(cur);dt.style.transform="translateX("+x+"px)";dt.style.opacity="1";const ro=new ResizeObserver(()=>{x=cx(tg||cur);kick()});ro.observe(ul);return()=>{cancelAnimationFrame(raf);ro.disconnect()}
});
