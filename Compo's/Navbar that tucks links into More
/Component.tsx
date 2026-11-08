// @ts-nocheck: setup() es JavaScript del DOM sin tipos, el mismo de la pestaña JS.
import { useEffect, useRef, type CSSProperties } from "react";

const css = `
.uvq12-pp {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  font-size: var(--s);
  color: var(--fg);
  -webkit-tap-highlight-color: transparent;
  width: calc(var(--w) + var(--d,0em));
  height: 15em;
  min-width: 12em;
  max-width: 30em;
}

.uvq12-pp nav {
  position: relative;
  display: flex;
  align-items: center;
  gap: .3em;
  height: 3em;
  padding: 0 .35em 0 .9em;
  border-radius: 1em;
  background: color-mix(in srgb,var(--fg) 2%,var(--bg));
  box-shadow: 0 0 0 1px color-mix(in srgb,var(--fg) 9%,transparent),0 1.4em 2.8em -1.4em rgba(0,0,0,.45);
}

.uvq12-pp ul {
  margin: 0;
  padding: 0;
  list-style: none;
}

.uvq12-pp .lg {
  display: flex;
  align-items: center;
  gap: .4em;
  flex: none;
  margin-right: .3em;
  font-weight: 800;
  letter-spacing: -.02em;
  color: var(--fg);
  text-decoration: none;
  border-radius: .4em;
}

.uvq12-pp .lg i {
  width: .8em;
  height: .8em;
  border-radius: .25em;
  background: var(--c1);
  transform: rotate(45deg);
}

.uvq12-pp .ls {
  position: relative;
  display: flex;
  flex: 1;
  min-width: 0;
  gap: .1em;
  overflow: hidden;
}

.uvq12-pp .ls li {
  flex: none;
}

.uvq12-pp .ls li[hidden],.uvq12-pp .mm li[hidden],.uvq12-pp .mr[hidden] {
  display: none;
}

.uvq12-pp .ls a {
  display: block;
  padding: .5em .65em;
  border-radius: .6em;
  font-size: .84em;
  font-weight: 600;
  white-space: nowrap;
  color: color-mix(in srgb,var(--fg) 55%,transparent);
  text-decoration: none;
  transition: background calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1),color calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq12-pp .ls a:hover {
  color: var(--fg);
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uvq12-pp a[aria-current] {
  color: var(--c1);
  background: var(--c2);
}

.uvq12-pp .ls a[aria-current] {
  color: var(--c1);
}

.uvq12-pp a:focus-visible,.uvq12-pp button:focus-visible {
  outline: 2px solid var(--c1);
  outline-offset: 2px;
}

.uvq12-pp .mr {
  position: relative;
  flex: none;
}

.uvq12-pp .mr button {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  display: flex;
  align-items: center;
  gap: .35em;
  padding: .5em .6em;
  border-radius: .6em;
  font-size: .84em;
  font-weight: 700;
  transition: background calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq12-pp .mr button:hover,.uvq12-pp .mr.open button {
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uvq12-pp .mr button:active {
  transform: scale(.96);
}

.uvq12-pp .mr b {
  display: grid;
  place-items: center;
  min-width: 1.5em;
  height: 1.5em;
  padding: 0 .3em;
  border-radius: 1em;
  font-size: .75em;
  color: #fff;
  background: var(--c1);
}

.uvq12-pp .mr b.bump {
  animation: uvq12-pp calc(0.4s / var(--k)) cubic-bezier(.34,1.56,.64,1);
}

.uvq12-pp .mr.hc b {
  box-shadow: 0 0 0 .2em var(--c2);
}

.uvq12-pp .mr svg {
  width: .9em;
  height: .9em;
  transition: transform calc(0.3s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq12-pp .mr.open svg {
  transform: rotate(180deg);
}

.uvq12-pp .mm {
  position: absolute;
  right: 0;
  top: calc(100% + .7em);
  z-index: 3;
  min-width: 9.5em;
  padding: .35em;
  border-radius: .9em;
  background: color-mix(in srgb,var(--fg) 2%,var(--bg));
  box-shadow: 0 0 0 1px color-mix(in srgb,var(--fg) 9%,transparent),0 1.4em 2.8em -1.4em rgba(0,0,0,.45);
  opacity: 0;
  visibility: hidden;
  transform: translateY(-.4em) scale(.97);
  transform-origin: 100% 0;
  transition: all calc(0.25s / var(--k)) cubic-bezier(.22,1,.36,1);
}

.uvq12-pp .mr.open .mm {
  opacity: 1;
  visibility: visible;
  transform: none;
}

.uvq12-pp .mm a {
  display: block;
  padding: .5em .65em;
  border-radius: .55em;
  font-size: .84em;
  font-weight: 600;
  color: var(--fg);
  text-decoration: none;
}

.uvq12-pp .mm a:hover {
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uvq12-pp .mm li {
  animation: uvq12-pp-in calc(0.3s / var(--k)) cubic-bezier(.22,1,.36,1);
}

.uvq12-pp .hd {
  position: absolute;
  right: -1em;
  top: 50%;
  width: .45em;
  height: 1.8em;
  margin-top: -.9em;
  border-radius: 1em;
  cursor: ew-resize;
  touch-action: none;
  background: repeating-linear-gradient(180deg,color-mix(in srgb,var(--fg) 55%,transparent) 0 2px,transparent 2px 5px) 50% 50%/2px 1.2em no-repeat,color-mix(in srgb,var(--fg) 6%,transparent);
}

@keyframes uvq12-pp {
  50% {
    transform: scale(1.35);
  }
}

@keyframes uvq12-pp-in {
  from {
    opacity: 0;
    transform: translateX(.6em);
  }
}

@media (prefers-reduced-motion:reduce) {
  .uvq12-pp,.uvq12-pp *,.uvq12-pp *::before,.uvq12-pp *::after {
    animation-duration: .01ms!important;
    animation-iteration-count: 1!important;
    transition-duration: .01ms!important;
  }
}
`;

// Interacción en JavaScript del DOM: la misma que en la pestaña JS.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function setup(el: any) {
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
}

export default function NavPriorityPlus() {
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
          "--c1": "#0f766e",
          "--c2": "#ccfbf1",
          "--s": "16px",
          "--k": "1",
          "--w": "24em",
          "--fg": "#1d1d1f",
          "--bg": "#f5f5f7",
        } as CSSProperties}
      >
        <div className="uvq12-pp"><nav aria-label="Main"><a className="lg" href="#"><i aria-hidden="true"></i>North</a><ul className="ls"><li><a href="#" aria-current="page">Home</a></li><li><a href="#">Product</a></li><li><a href="#">Pricing</a></li><li><a href="#">Customers</a></li><li><a href="#">Blog</a></li><li><a href="#">Docs</a></li><li><a href="#">Careers</a></li><li><a href="#">Contact</a></li></ul><div className="mr"><button type="button" aria-expanded="false" aria-controls="uvq12-pp-m"><span>More</span><b>0</b><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 10l5 5 5-5" /></svg></button><ul className="mm" id="uvq12-pp-m"><li><a href="#" aria-current="page">Home</a></li><li><a href="#">Product</a></li><li><a href="#">Pricing</a></li><li><a href="#">Customers</a></li><li><a href="#">Blog</a></li><li><a href="#">Docs</a></li><li><a href="#">Careers</a></li><li><a href="#">Contact</a></li></ul></div><span className="hd" aria-hidden="true"></span></nav></div>
      </div>
    </>
  );
}
