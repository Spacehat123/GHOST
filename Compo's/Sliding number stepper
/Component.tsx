// @ts-nocheck: setup() es JavaScript del DOM sin tipos, el mismo de la pestaña JS.
import { useEffect, useRef, type CSSProperties } from "react";

const css = `
.uv-in2q {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  font-size: var(--s);
  color: var(--fg);
  -webkit-tap-highlight-color: transparent;
  display: inline-flex;
  align-items: center;
  gap: .2em;
  padding: .35em;
  border-radius: 999px;
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uv-in2q button {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  display: grid;
  place-items: center;
  width: 2.3em;
  height: 2.3em;
  font-size: calc(var(--s) * 1.1);
  font-weight: 500;
  color: var(--fg);
  background: var(--bg);
  border: 0;
  border-radius: 50%;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(0,0,0,.09);
  transition: all calc(0.3s / var(--k)) cubic-bezier(.34,1.56,.64,1);
}

.uv-in2q button:hover {
  color: var(--c1);
  scale: 1.08;
}

.uv-in2q button:active {
  scale: .9;
}

.uv-in2q button:disabled {
  opacity: .35;
  cursor: not-allowed;
  scale: 1;
}

.uv-in2q .nm {
  position: relative;
  display: grid;
  place-items: center;
  width: 2.6em;
  height: 2.3em;
  overflow: hidden;
  font-variant-numeric: tabular-nums;
  font-weight: 650;
}

.uv-in2q .nm b {
  position: absolute;
}

.uv-in2q .nm b.up {
  animation: uv-in2q-u calc(0.35s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uv-in2q .nm b.dn {
  animation: uv-in2q-d calc(0.35s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

@keyframes uv-in2q-u {
  from {
    transform: translateY(1.4em);
    opacity: 0;
  }
  to {
    transform: none;
    opacity: 1;
  }
}

@keyframes uv-in2q-d {
  from {
    transform: translateY(-1.4em);
    opacity: 0;
  }
  to {
    transform: none;
    opacity: 1;
  }
}
`;

// Interacción en JavaScript del DOM: la misma que en la pestaña JS.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function setup(el: any) {
  let n=1;const b=el.querySelector(".nm b"),mn=el.querySelector(".mn");const set=(v,up)=>{if(v<1||v>99)return;n=v;b.textContent=n;b.className=up?"up":"dn";mn.disabled=n<=1};el.querySelector(".pl").addEventListener("click",()=>set(n+1,true));mn.addEventListener("click",()=>set(n-1,false));set(1,true)
}

export default function In2Qty() {
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
        <div className="uv-in2q"><button className="mn">−</button><span className="nm"><b>1</b></span><button className="pl">+</button></div>
      </div>
    </>
  );
}
