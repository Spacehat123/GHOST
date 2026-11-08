// @ts-nocheck: setup() es JavaScript del DOM sin tipos, el mismo de la pestaña JS.
import { useEffect, useRef, type CSSProperties } from "react";

const css = `
.uv-in2v {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  font-size: var(--s);
  color: var(--fg);
  -webkit-tap-highlight-color: transparent;
  position: relative;
  display: block;
  width: 16.5em;
}

.uv-in2v input {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  width: 100%;
  box-sizing: border-box;
  padding: .9em 3em .9em 1em;
  font-size: var(--s);
  color: var(--fg);
  background: color-mix(in srgb,var(--fg) 5%,transparent);
  border: 0;
  outline: 0;
  border-radius: .85em;
  box-shadow: inset 0 0 0 1.6px color-mix(in srgb,var(--fg) 10%,transparent);
  transition: box-shadow calc(0.3s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uv-in2v input:focus {
  box-shadow: inset 0 0 0 1.8px var(--c1);
}

.uv-in2v .ic {
  position: absolute;
  right: 1em;
  top: 1.25em;
  display: grid;
  place-items: center;
  width: 1.3em;
  height: 1.3em;
}

.uv-in2v .ic svg {
  position: absolute;
  width: 1.15em;
  height: 1.15em;
  opacity: 0;
  scale: .5;
  transition: all calc(0.35s / var(--k)) cubic-bezier(.34,1.56,.64,1);
}

.uv-in2v .ok {
  color: #34c759;
}

.uv-in2v .ok path {
  stroke-dasharray: 1;
  stroke-dashoffset: 1;
  transition: stroke-dashoffset calc(0.35s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uv-in2v .no {
  color: #ff375f;
}

.uv-in2v .hp {
  display: block;
  margin-top: .5em;
  padding-left: .2em;
  font-size: calc(var(--s) * .74);
  font-style: normal;
  color: #ff375f;
  opacity: 0;
  transform: translateY(-.3em);
  transition: all calc(0.3s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uv-in2v.ok input {
  box-shadow: inset 0 0 0 1.8px #34c759;
}

.uv-in2v.ok .ok {
  opacity: 1;
  scale: 1;
}

.uv-in2v.ok .ok path {
  stroke-dashoffset: 0;
}

.uv-in2v.bad input {
  box-shadow: inset 0 0 0 1.8px #ff375f;
  animation: uv-in2v calc(0.42s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uv-in2v.bad .no {
  opacity: 1;
  scale: 1;
}

.uv-in2v.bad .hp {
  opacity: 1;
  transform: none;
}

@keyframes uv-in2v {
  0%,100% {
    translate: 0;
  }
  20% {
    translate: -6px;
  }
  40% {
    translate: 5px;
  }
  60% {
    translate: -3px;
  }
  80% {
    translate: 2px;
  }
}
`;

// Interacción en JavaScript del DOM: la misma que en la pestaña JS.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function setup(el: any) {
  const i=el.querySelector("input");const ck=()=>{const v=i.value.trim();el.classList.remove("ok","bad");if(!v)return;el.classList.add(/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(v)?"ok":"bad")};i.addEventListener("input",()=>{if(el.classList.contains("bad"))el.classList.remove("bad")});i.addEventListener("blur",ck);i.addEventListener("keydown",e=>{if(e.key==="Enter")ck()})
}

export default function In2Valid() {
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
          "--c1": "#0071e3",
          "--c2": "#8ec5ff",
          "--s": "16px",
          "--k": "1",
          "--fg": "#1d1d1f",
          "--bg": "#f5f5f7",
        } as CSSProperties}
      >
        <label className="uv-in2v"><input placeholder="you@email.com" /><span className="ic"><svg className="ok" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" pathLength="1" /></svg><svg className="no" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></span><em className="hp">Enter a valid email</em></label>
      </div>
    </>
  );
}
