// @ts-nocheck: setup() es JavaScript del DOM sin tipos, el mismo de la pestaña JS.
import { useEffect, useRef, type CSSProperties } from "react";

const css = `
.uv-swp {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  font-size: var(--s);
  color: var(--fg);
  -webkit-tap-highlight-color: transparent;
  position: relative;
  width: 17em;
  height: 3.6em;
  padding: .3em;
  border-radius: 999px;
  background: color-mix(in srgb,var(--fg) 7%,transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb,var(--fg) 10%,transparent);
  user-select: none;
  touch-action: none;
  --x: 0px;
  --p: 0;
}

.uv-swp::before {
  content: "";
  position: absolute;
  left: .3em;
  top: .3em;
  bottom: .3em;
  width: calc(3em + var(--x));
  border-radius: 999px;
  background: linear-gradient(90deg,var(--c2),var(--c1));
  transition: width calc(0.5s / var(--k)) cubic-bezier(.22,1,.36,1);
}

.uv-swp .tx,.uv-swp .ok {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding-left: 2.6em;
  font-weight: 600;
  pointer-events: none;
  transition: opacity calc(0.3s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uv-swp .tx {
  opacity: calc(1 - var(--p) * 1.8);
  background: linear-gradient(90deg,color-mix(in srgb,var(--fg) 45%,transparent) 30%,var(--fg) 50%,color-mix(in srgb,var(--fg) 45%,transparent) 70%) 0 0/250% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: uv-swp calc(2.6s / var(--k)) linear infinite;
}

.uv-swp .ok {
  opacity: 0;
  padding: 0 3.6em 0 0;
  color: #fff;
}

.uv-swp .kn {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 3em;
  height: 3em;
  border-radius: 50%;
  color: var(--c1);
  background: #fff;
  box-shadow: 0 4px 12px -4px rgba(0,0,0,.35);
  transform: translateX(var(--x));
  transition: transform calc(0.5s / var(--k)) cubic-bezier(.22,1,.36,1);
  cursor: grab;
  touch-action: none;
}

.uv-swp .kn svg {
  grid-area: 1/1;
  width: 1.3em;
  height: 1.3em;
  transition: opacity calc(0.25s / var(--k)) cubic-bezier(.25,.1,.25,1),scale calc(0.35s / var(--k)) cubic-bezier(.34,1.56,.64,1);
}

.uv-swp .ck {
  opacity: 0;
  scale: .4;
}

.uv-swp.is-drag::before,.uv-swp.is-drag .kn {
  transition: none;
}

.uv-swp.is-drag .kn {
  cursor: grabbing;
}

.uv-swp.is-done .ok {
  opacity: 1;
}

.uv-swp.is-done .kn svg:first-child {
  opacity: 0;
}

.uv-swp.is-done .ck {
  opacity: 1;
  scale: 1;
}

.uv-swp .kn:focus-visible {
  outline: 2px solid var(--c1);
  outline-offset: 3px;
}

@keyframes uv-swp {
  from {
    background-position: 100% 0;
  }
  to {
    background-position: -150% 0;
  }
}

@media (prefers-reduced-motion:reduce) {
  .uv-swp,.uv-swp *,.uv-swp *::before,.uv-swp *::after {
    animation-duration: .01ms!important;
    animation-iteration-count: 1!important;
    transition-duration: .01ms!important;
  }
}
`;

// Interacción en JavaScript del DOM: la misma que en la pestaña JS.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function setup(el: any) {
  const k=el.querySelector(".kn");let sx=0,dr=false,max=0,p=0,sc=1,tm=0;
const set=v=>{p=Math.max(0,Math.min(1,v));el.style.setProperty("--p",p.toFixed(3));el.style.setProperty("--x",(p*max).toFixed(1)+"px")};
const measure=()=>{max=el.clientWidth-k.offsetWidth-parseFloat(getComputedStyle(el).paddingLeft)*2;sc=el.getBoundingClientRect().width/el.offsetWidth||1};
const done=()=>{measure();el.classList.add("is-done");set(1);clearTimeout(tm);tm=setTimeout(()=>{el.classList.remove("is-done");set(0)},2400)};
k.addEventListener("pointerdown",e=>{if(el.classList.contains("is-done"))return;measure();dr=true;sx=e.clientX;k.setPointerCapture(e.pointerId);el.classList.add("is-drag")});
k.addEventListener("pointermove",e=>{if(dr)set((e.clientX-sx)/sc/max)});
const up=()=>{if(!dr)return;dr=false;el.classList.remove("is-drag");p>.85?done():set(0)};
k.addEventListener("pointerup",up);k.addEventListener("pointercancel",up);
k.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();done()}});
return()=>clearTimeout(tm)
}

export default function BtnSwipeConfirm() {
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
          "--c1": "#12a36b",
          "--c2": "#b9f5d8",
          "--s": "16px",
          "--k": "1",
          "--fg": "#1d1d1f",
          "--bg": "#f5f5f7",
        } as CSSProperties}
      >
        <div className="uv-swp"><span className="tx">Slide to pay</span><span className="ok">Payment confirmed</span><button className="kn" type="button" aria-label="Confirm payment"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg><svg className="ck" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg></button></div>
      </div>
    </>
  );
}
