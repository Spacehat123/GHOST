// @ts-nocheck: setup() es JavaScript del DOM sin tipos, el mismo de la pestaña JS.
import { useEffect, useRef, type CSSProperties } from "react";

const css = `
.uvq12-ml {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  font-size: var(--s);
  color: var(--fg);
  -webkit-tap-highlight-color: transparent;
  position: relative;
  width: max-content;
  padding: .4em .5em .9em;
  border-radius: 1.2em;
  background: color-mix(in srgb,var(--fg) 2%,var(--bg));
  box-shadow: 0 0 0 1px color-mix(in srgb,var(--fg) 9%,transparent),0 1.4em 2.8em -1.4em rgba(0,0,0,.45);
}

.uvq12-ml ul {
  display: flex;
  gap: .15em;
  margin: 0;
  padding: 0;
  list-style: none;
}

.uvq12-ml a {
  display: block;
  padding: .6em .75em;
  border-radius: .8em;
  font-size: .9em;
  font-weight: 650;
  color: color-mix(in srgb,var(--fg) 55%,transparent);
  text-decoration: none;
  transition: transform calc(0.5s / var(--k)) cubic-bezier(.34,1.56,.64,1),color calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1),background calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq12-ml a.mg {
  transition: transform calc(0.12s / var(--k)) linear,color calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1),background calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq12-ml a:hover {
  color: var(--fg);
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uvq12-ml a:active {
  background: color-mix(in srgb,var(--c1) 12%,transparent);
}

.uvq12-ml a[aria-current] {
  color: var(--fg);
}

.uvq12-ml a:focus-visible {
  outline: 2px solid var(--c1);
  outline-offset: 1px;
}

.uvq12-ml .dt {
  position: absolute;
  left: 0;
  bottom: .45em;
  opacity: 0;
  transition: opacity calc(0.3s / var(--k)) cubic-bezier(.25,.1,.25,1);
  width: .45em;
  height: .45em;
  margin-left: -.225em;
  border-radius: 1em;
  background: linear-gradient(90deg,var(--c2),var(--c1));
  box-shadow: 0 0 .6em color-mix(in srgb,var(--c1) 60%,transparent);
  will-change: transform;
}

@media (prefers-reduced-motion:reduce) {
  .uvq12-ml,.uvq12-ml *,.uvq12-ml *::before,.uvq12-ml *::after {
    animation-duration: .01ms!important;
    animation-iteration-count: 1!important;
    transition-duration: .01ms!important;
  }
}
`;

// Interacción en JavaScript del DOM: la misma que en la pestaña JS.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function setup(el: any) {
  const ul=el.querySelector("ul"),ls=[...el.querySelectorAll("a")],dt=el.querySelector(".dt");let cur=ls[0],tg=null,x=0,v=0,raf=0,last=0;
const rm=matchMedia("(prefers-reduced-motion: reduce)").matches;const cx=a=>a.parentElement.offsetLeft+a.parentElement.offsetWidth/2;
const loop=t=>{const dt0=Math.min(32,t-(last||t))/16.7;last=t;const g=cx(tg||cur);v+=(g-x)*.14*dt0;v*=Math.pow(.74,dt0);x+=v*dt0;const s=1+Math.min(Math.abs(v)/6,2.2);dt.style.transform="translateX("+x.toFixed(2)+"px) scaleX("+s.toFixed(3)+")";if(Math.abs(g-x)<.2&&Math.abs(v)<.05){x=g;dt.style.transform="translateX("+x+"px)";raf=0;last=0;return}raf=requestAnimationFrame(loop)};
const kick=()=>{if(rm){x=cx(tg||cur);dt.style.transform="translateX("+x+"px)";return}if(!raf)raf=requestAnimationFrame(loop)};
ls.forEach(a=>{a.addEventListener("pointerenter",()=>{tg=a;kick()});a.addEventListener("pointermove",e=>{if(rm||e.pointerType!=="mouse")return;const r=a.getBoundingClientRect();a.classList.add("mg");a.style.transform="translate("+((e.clientX-r.left-r.width/2)*.3).toFixed(1)+"px,"+((e.clientY-r.top-r.height/2)*.35).toFixed(1)+"px)"});a.addEventListener("pointerleave",()=>{a.classList.remove("mg");a.style.transform="";tg=null;kick()});
a.addEventListener("focus",()=>{tg=a;kick()});a.addEventListener("blur",()=>{tg=null;kick()});
a.addEventListener("click",e=>{e.preventDefault();cur=a;ls.forEach(o=>{if(o===a)o.setAttribute("aria-current","page");else o.removeAttribute("aria-current")});kick()});
a.addEventListener("keydown",e=>{const i=ls.indexOf(a),n=e.key==="ArrowRight"?i+1:e.key==="ArrowLeft"?i-1:-1;if(n>=0&&n<ls.length){e.preventDefault();ls[n].focus()}})});
x=cx(cur);dt.style.transform="translateX("+x+"px)";dt.style.opacity="1";const ro=new ResizeObserver(()=>{x=cx(tg||cur);kick()});ro.observe(ul);return()=>{cancelAnimationFrame(raf);ro.disconnect()}
}

export default function NavMagnetLinks() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current?.firstElementChild;
    return el ? setup(el) : undefined;
  }, []);

  return (
    <>
      <style>{css}</style>
      <div
        ref={ref} style={{
          "--c1": "#e11d48",
          "--c2": "#fb923c",
          "--s": "16px",
          "--k": "1",
          "--fg": "#1d1d1f",
          "--bg": "#f5f5f7",
        } as CSSProperties}
      >
        <nav className="uvq12-ml" aria-label="Navigation"><ul><li><a href="#" aria-current="page">Work</a></li><li><a href="#">Studio</a></li><li><a href="#">Blog</a></li><li><a href="#">Shop</a></li><li><a href="#">Contact</a></li></ul><span className="dt" aria-hidden="true"></span></nav>
      </div>
    </>
  );
}
