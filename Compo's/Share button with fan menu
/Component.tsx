// @ts-nocheck: setup() es JavaScript del DOM sin tipos, el mismo de la pestaña JS.
import { useEffect, useRef, type CSSProperties } from "react";

const css = `
.uvq15-shr {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  font-size: var(--s);
  color: var(--fg);
  -webkit-tap-highlight-color: transparent;
  display: grid;
  justify-items: center;
  width: 15em;
  padding-top: 5.6em;
}

.uvq15-shr .fan {
  position: relative;
  display: grid;
  place-items: center;
}

.uvq15-shr .mn {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  position: relative;
  z-index: 2;
  display: grid;
  place-items: center;
  width: 3.6em;
  height: 3.6em;
  border-radius: 50%;
  color: #fff;
  background: var(--c1);
  box-shadow: 0 .8em 1.6em -.8em rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.15);
  transition: transform calc(0.45s / var(--k)) cubic-bezier(.34,1.56,.64,1);
}

.uvq15-shr .mn:hover {
  transform: scale(1.05);
}

.uvq15-shr .mn:active {
  transform: scale(.94);
}

.uvq15-shr .mn:focus-visible,.uvq15-shr .it:focus-visible {
  outline: 2px solid var(--c1);
  outline-offset: 3px;
}

.uvq15-shr .mn>span {
  grid-area: 1/1;
  display: grid;
  transition: transform calc(0.4s / var(--k)) cubic-bezier(.22,1,.36,1),opacity calc(0.25s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq15-shr .mn svg {
  width: 1.4em;
  height: 1.4em;
}

.uvq15-shr .x {
  opacity: 0;
  transform: rotate(-90deg) scale(.5);
}

.uvq15-shr.open .o {
  opacity: 0;
  transform: rotate(90deg) scale(.5);
}

.uvq15-shr.open .x {
  opacity: 1;
  transform: none;
}

.uvq15-shr.open .mn {
  background: var(--c2);
  color: #1a2e05;
}

.uvq15-shr .it {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  position: absolute;
  left: 50%;
  top: 50%;
  display: grid;
  place-items: center;
  width: 2.9em;
  height: 2.9em;
  margin: -1.45em 0 0 -1.45em;
  border-radius: 50%;
  color: var(--fg);
  background: var(--bg);
  box-shadow: 0 0 0 1px color-mix(in srgb,var(--fg) 10%,transparent),0 .6em 1.2em -.6em rgba(0,0,0,.4);
  opacity: 0;
  visibility: hidden;
  transform: scale(.3);
  transition: transform calc(0.3s / var(--k)) cubic-bezier(.25,.1,.25,1),opacity calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1),visibility 0s calc(0.3s / var(--k)),background calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1),color calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq15-shr .it svg {
  width: 1.2em;
  height: 1.2em;
}

.uvq15-shr.open .it {
  opacity: 1;
  visibility: visible;
  transform: translate(var(--x),var(--y));
  transition: transform calc(0.55s / var(--k)) cubic-bezier(.34,1.56,.64,1) calc(var(--i) * calc(0.05s / var(--k))),opacity calc(0.25s / var(--k)) cubic-bezier(.25,.1,.25,1) calc(var(--i) * calc(0.05s / var(--k))),visibility 0s,background calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1),color calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq15-shr.open .it:hover,.uvq15-shr.open .it:focus-visible {
  color: #1a2e05;
  background: var(--c2);
  transform: translate(var(--x),var(--y)) scale(1.12);
}

.uvq15-shr .it::after {
  content: attr(aria-label);
  position: absolute;
  bottom: calc(100% + .5em);
  left: 50%;
  padding: .3em .6em;
  border-radius: .5em;
  font-size: .68em;
  font-weight: 700;
  white-space: nowrap;
  color: var(--bg);
  background: var(--fg);
  translate: -50% .3em;
  opacity: 0;
  pointer-events: none;
  transition: opacity calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1),translate calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq15-shr .it:hover::after,.uvq15-shr .it:focus-visible::after {
  opacity: 1;
  translate: -50% 0;
}

.uvq15-shr .it:hover,.uvq15-shr .it:focus-visible {
  z-index: 3;
}

.uvq15-shr .tt {
  height: 1.4em;
  margin: .9em 0 0;
  font-size: .8em;
  font-weight: 700;
  color: color-mix(in srgb,var(--fg) 55%,transparent);
  opacity: 0;
  transform: translateY(.4em);
  transition: opacity calc(0.25s / var(--k)) cubic-bezier(.25,.1,.25,1),transform calc(0.35s / var(--k)) cubic-bezier(.22,1,.36,1);
}

.uvq15-shr .tt.on {
  opacity: 1;
  transform: none;
}

@media (prefers-reduced-motion:reduce) {
  .uvq15-shr,.uvq15-shr *,.uvq15-shr *::before,.uvq15-shr *::after {
    animation-duration: .01ms!important;
    animation-iteration-count: 1!important;
    transition-duration: .01ms!important;
  }
}
`;

// Interacción en JavaScript del DOM: la misma que en la pestaña JS.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function setup(el: any) {
  const k=parseFloat(getComputedStyle(el).getPropertyValue("--k"))||1,rm=matchMedia("(prefers-reduced-motion:reduce)").matches;const mn=el.querySelector(".mn"),its=[...el.querySelectorAll(".it")],tt=el.querySelector(".tt");let tm=0;
const set=o=>{el.classList.toggle("open",o);mn.setAttribute("aria-expanded",String(o));its.forEach(b=>b.tabIndex=o?0:-1)};
mn.addEventListener("click",()=>set(!el.classList.contains("open")));
its.forEach((b,i)=>{b.addEventListener("click",()=>{if(i===0&&navigator.clipboard)navigator.clipboard.writeText(location.href).catch(()=>{});tt.textContent=b.dataset.m;tt.classList.add("on");clearTimeout(tm);tm=setTimeout(()=>tt.classList.remove("on"),1800/k);set(false);mn.focus()});
b.addEventListener("keydown",e=>{const d=e.key==="ArrowRight"||e.key==="ArrowDown"?1:e.key==="ArrowLeft"||e.key==="ArrowUp"?-1:0;if(d){e.preventDefault();its[(i+d+its.length)%its.length].focus()}})});
el.addEventListener("keydown",e=>{if(e.key==="Escape"&&el.classList.contains("open")){set(false);mn.focus()}});
mn.addEventListener("keydown",e=>{if(e.key==="ArrowUp"&&el.classList.contains("open")){e.preventDefault();its[0].focus()}});
const out=e=>{if(!el.contains(e.target))set(false)};document.addEventListener("pointerdown",out);return()=>{clearTimeout(tm);document.removeEventListener("pointerdown",out)}
}

export default function Mi27ShareFan() {
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
          "--c1": "#1f2937",
          "--c2": "#84cc16",
          "--s": "16px",
          "--k": "1",
          "--fg": "#1d1d1f",
          "--bg": "#f5f5f7",
        } as CSSProperties}
      >
        <div className="uvq15-shr"><div className="fan"><button type="button" className="it" tabIndex={-1} style={{ "--i": "0", "--x": "-4.83em", "--y": "-1.29em" } as CSSProperties} aria-label="Copy link" data-m="Link copied"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1" /><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" /></svg></button><button type="button" className="it" tabIndex={-1} style={{ "--i": "1", "--x": "-2.11em", "--y": "-4.53em" } as CSSProperties} aria-label="Mail" data-m="Opening email…"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></svg></button><button type="button" className="it" tabIndex={-1} style={{ "--i": "2", "--x": "2.11em", "--y": "-4.53em" } as CSSProperties} aria-label="Message" data-m="Opening messages…"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z" /></svg></button><button type="button" className="it" tabIndex={-1} style={{ "--i": "3", "--x": "4.83em", "--y": "-1.29em" } as CSSProperties} aria-label="QR code" data-m="QR code ready"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 20h4v-3" /></svg></button><button type="button" className="mn" aria-expanded="false" aria-label="Share"><span className="or"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4" /></svg></span><span className="x"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg></span></button></div><p className="tt" aria-live="polite"></p></div>
      </div>
    </>
  );
}
