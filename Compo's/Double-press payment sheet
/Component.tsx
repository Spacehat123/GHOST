// @ts-nocheck: setup() es JavaScript del DOM sin tipos, el mismo de la pestaña JS.
import { useEffect, useRef, type CSSProperties } from "react";

const css = `
.uvq13-pay {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  font-size: var(--s);
  color: var(--fg);
  -webkit-tap-highlight-color: transparent;
  position: relative;
  width: 22em;
  height: 19em;
  border-radius: 1.2em;
  overflow: hidden;
  background: color-mix(in srgb,var(--fg) 3%,var(--bg));
  box-shadow: 0 0 0 1px color-mix(in srgb,var(--fg) 9%,transparent);
}

.uvq13-pay .pr {
  display: flex;
  align-items: center;
  gap: .8em;
  padding: 1.1em;
}

.uvq13-pay .im {
  flex: none;
  width: 4em;
  height: 4em;
  border-radius: 1em;
  background: radial-gradient(circle at 50% 78%,transparent 1.25em,#1f2937 1.28em 1.5em,transparent 1.53em) 0 -.3em/100% 100% no-repeat,radial-gradient(ellipse .55em .75em at 24% 66%,#1f2937 98%,transparent),radial-gradient(ellipse .55em .75em at 76% 66%,#1f2937 98%,transparent),linear-gradient(160deg,#fecdd3,#fb7185);
}

.uvq13-pay .nm {
  margin: 0;
  font-size: .88em;
  font-weight: 750;
}

.uvq13-pay .pc {
  margin: .1em 0 0;
  font-size: .8em;
  color: color-mix(in srgb,var(--fg) 55%,transparent);
}

.uvq13-pay .fire {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  margin-left: auto;
  padding: .6em 1.1em;
  border-radius: 2em;
  font-size: .8em;
  font-weight: 700;
  color: var(--bg);
  background: var(--fg);
  transition: transform calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-pay .fire:active {
  transform: scale(.95);
}

.uvq13-pay .ln {
  display: grid;
  gap: .55em;
  padding: 0 1.1em;
}

.uvq13-pay .ln i {
  height: .6em;
  border-radius: 1em;
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uvq13-pay .ln .s {
  width: 60%;
}

.uvq13-pay .side {
  position: absolute;
  right: 0;
  top: 4.6em;
  z-index: 6;
  width: .32em;
  height: 2.8em;
  border-radius: .3em 0 0 .3em;
  background: color-mix(in srgb,var(--fg) 35%,transparent);
  cursor: pointer;
  transition: background calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-pay.open[data-st="idle"] .side,.uvq13-pay[data-st="arm"] .side {
  background: var(--c2);
  animation: uvq13-pay-s calc(1s / var(--k)) ease-in-out infinite;
}

.uvq13-pay .scr {
  position: absolute;
  inset: 0;
  z-index: 4;
  background: rgba(10,12,20,.4);
  opacity: 0;
  pointer-events: none;
  transition: opacity calc(0.3s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-pay.open .scr {
  opacity: 1;
  pointer-events: auto;
}

.uvq13-pay .sh {
  position: absolute;
  left: .6em;
  right: .6em;
  bottom: .6em;
  z-index: 5;
  padding: .85em 1em 1em;
  box-sizing: border-box;
  border-radius: 1.4em;
  background: color-mix(in srgb,var(--fg) 2%,var(--bg));
  box-shadow: 0 0 0 1px color-mix(in srgb,var(--fg) 9%,transparent),0 1.6em 3.2em -1.2em rgba(0,0,0,.5);
  background: var(--bg);
  transform: translateY(112%);
  transition: transform calc(0.5s / var(--k)) cubic-bezier(.22,1,.36,1);
}

.uvq13-pay.open .sh {
  transform: none;
}

.uvq13-pay header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.uvq13-pay header b {
  font-size: 1em;
  font-weight: 800;
}

.uvq13-pay .x {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  display: grid;
  place-items: center;
  width: 1.9em;
  height: 1.9em;
  border-radius: 50%;
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uvq13-pay .x svg {
  width: .9em;
  height: .9em;
}

.uvq13-pay .mid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: .8em;
  align-items: center;
  margin: .7em 0 .2em;
}

.uvq13-pay .cc {
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: flex-end;
  width: 5.6em;
  height: 3.6em;
  padding: .4em .5em;
  box-sizing: border-box;
  border-radius: .5em;
  color: #fff;
  background: linear-gradient(135deg,#0f172a,#334155 55%,#0ea5e9);
  box-shadow: 0 .4em .8em -.4em rgba(0,0,0,.5);
}

.uvq13-pay .chip {
  position: absolute;
  left: .5em;
  top: .55em;
  width: 1em;
  height: .75em;
  border-radius: .15em;
  background: linear-gradient(135deg,#fde68a,#d97706);
}

.uvq13-pay .cc span {
  font: 600 .58em/1 ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  letter-spacing: .08em;
}

.uvq13-pay .rows {
  display: grid;
  margin: 0;
}

.uvq13-pay .rows div {
  display: flex;
  justify-content: space-between;
  gap: .5em;
  padding: .25em 0;
  font-size: .66em;
  border-bottom: 1px solid color-mix(in srgb,var(--fg) 9%,transparent);
}

.uvq13-pay dt {
  color: color-mix(in srgb,var(--fg) 55%,transparent);
}

.uvq13-pay dd {
  margin: 0;
  font-weight: 600;
  text-align: right;
}

.uvq13-pay .rows .tt {
  font-size: .8em;
  border: 0;
}

.uvq13-pay .tt dd {
  font-weight: 850;
}

.uvq13-pay .cf {
  display: flex;
  align-items: center;
  gap: .7em;
  margin: .6em 0 .7em;
}

.uvq13-pay .bio {
  flex: none;
  width: 2.6em;
  height: 2.6em;
  color: var(--fg);
  transition: color calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-pay .bio svg {
  width: 100%;
  height: 100%;
  overflow: visible;
}

.uvq13-pay .bio .br,.uvq13-pay .bio .fc {
  transform-box: fill-box;
  transform-origin: center;
  transition: opacity calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-pay .bio .ci,.uvq13-pay .bio .ck {
  stroke: var(--c2);
  stroke-dasharray: 1;
  stroke-dashoffset: 1;
  transition: stroke-dashoffset calc(0.45s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-pay [data-st="scan"] .bio {
  color: var(--c2);
}

.uvq13-pay [data-st="scan"] .bio .br {
  animation: uvq13-pay-b calc(0.45s / var(--k)) ease-in-out infinite alternate;
}

.uvq13-pay [data-st="done"] .bio .br,.uvq13-pay [data-st="done"] .bio .fc {
  opacity: 0;
}

.uvq13-pay [data-st="done"] .bio .ci,.uvq13-pay [data-st="done"] .bio .ck {
  stroke-dashoffset: 0;
}

.uvq13-pay [data-st="done"] .bio .ck {
  transition-delay: calc(.25s / var(--k));
}

.uvq13-pay .msg {
  flex: 1;
  display: grid;
  margin: 0;
  font-size: .8em;
  font-weight: 700;
}

.uvq13-pay .msg span {
  grid-area: 1/1;
  opacity: 0;
  transition: opacity calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-pay [data-st="idle"] .m0,.uvq13-pay [data-st="arm"] .m1,.uvq13-pay [data-st="scan"] .m2,.uvq13-pay [data-st="done"] .m3 {
  opacity: 1;
}

.uvq13-pay .m1,.uvq13-pay .m3 {
  color: var(--c2);
}

.uvq13-pay .ar {
  flex: none;
  width: 1.3em;
  height: 1.3em;
  color: color-mix(in srgb,var(--fg) 55%,transparent);
  animation: uvq13-pay-a calc(1s / var(--k)) ease-in-out infinite;
}

.uvq13-pay .ar svg {
  width: 100%;
  height: 100%;
}

.uvq13-pay [data-st="scan"] .ar,.uvq13-pay [data-st="done"] .ar {
  opacity: 0;
}

.uvq13-pay .go {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  width: 100%;
  padding: .75em;
  border-radius: .8em;
  font-size: .85em;
  font-weight: 700;
  color: var(--bg);
  background: var(--fg);
  transition: transform calc(0.15s / var(--k)) cubic-bezier(.25,.1,.25,1),background calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-pay .go:active {
  transform: scale(.98);
}

.uvq13-pay [data-st="arm"] .go {
  background: var(--c2);
  color: #04210f;
}

.uvq13-pay button:focus-visible {
  outline: 2px solid var(--c1);
  outline-offset: 3px;
}

.uvq13-pay .tst {
  position: absolute;
  left: 50%;
  top: 1em;
  z-index: 7;
  margin: 0;
  padding: .5em 1em;
  border-radius: 2em;
  font-size: .78em;
  font-weight: 700;
  white-space: nowrap;
  color: #04210f;
  background: var(--c2);
  opacity: 0;
  transform: translate(-50%,-.6em);
  pointer-events: none;
  transition: opacity calc(0.25s / var(--k)) cubic-bezier(.25,.1,.25,1),transform calc(0.35s / var(--k)) cubic-bezier(.34,1.56,.64,1);
}

.uvq13-pay .tst.on {
  opacity: 1;
  transform: translate(-50%,0);
}

@keyframes uvq13-pay-s {
  50% {
    transform: translateX(-.25em);
  }
}

@keyframes uvq13-pay-b {
  to {
    transform: scale(.86);
  }
}

@keyframes uvq13-pay-a {
  50% {
    transform: translateX(.25em);
  }
}

@media (prefers-reduced-motion:reduce) {
  .uvq13-pay,.uvq13-pay *,.uvq13-pay *::before,.uvq13-pay *::after {
    animation-duration: .01ms!important;
    animation-iteration-count: 1!important;
    transition-duration: .01ms!important;
  }
}
`;

// Interacción en JavaScript del DOM: la misma que en la pestaña JS.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function setup(el: any) {
  const fire=el.querySelector(".fire"),scr=el.querySelector(".scr"),pnl=el.querySelector(".sh");let tms=[];const later=(f,ms)=>tms.push(setTimeout(f,ms));
const foc=(t,ms)=>later(()=>{const n=typeof t==="string"?el.querySelector(t):t;n&&n.focus({preventScroll:true})},ms||0);
const isOpen=()=>el.classList.contains("open");
const open=u=>{if(isOpen())return;el.classList.add("open");pnl.inert=false;fire&&fire.setAttribute("aria-expanded","true");st('idle');u&&foc('.go',380)};
const close=()=>{if(!isOpen())return;el.classList.remove("open");pnl.inert=true;fire&&fire.setAttribute("aria-expanded","false");clearTimeout(at);st('idle');fire&&fire.focus({preventScroll:true})};
fire&&fire.addEventListener("click",()=>isOpen()?close():open(true));scr&&scr.addEventListener("click",()=>close());
el.addEventListener("keydown",e=>{if(!isOpen())return;if(e.key==="Escape"){e.preventDefault();e.stopPropagation();close()}else if(e.key==="Tab"){const f=[...pnl.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea,a[href],[tabindex="0"]')].filter(x=>x.getClientRects().length);if(!f.length){e.preventDefault();return}const a=f[0],z=f[f.length-1],ac=document.activeElement;if(e.shiftKey&&(ac===a||!pnl.contains(ac))){e.preventDefault();z.focus()}else if(!e.shiftKey&&(ac===z||!pnl.contains(ac))){e.preventDefault();a.focus()}}});
pnl.inert=true;later(()=>open(false),500);const sh=el.querySelector(".sh"),tst=el.querySelector(".tst");let at=0;
const st=s=>{sh.dataset.st=s;el.dataset.st=s};
const press=()=>{if(!isOpen())return;const s=sh.dataset.st;if(s==="idle"){st("arm");clearTimeout(at);at=setTimeout(()=>sh.dataset.st==="arm"&&st("idle"),1500)}else if(s==="arm"){clearTimeout(at);st("scan");later(()=>{st("done");later(()=>{close();tst.classList.add("on");later(()=>tst.classList.remove("on"),2000)},900)},1100)}};
el.querySelector(".go").addEventListener("click",press);el.querySelector(".side").addEventListener("click",press);el.querySelector(".sh .x").addEventListener("click",close);return()=>{tms.forEach(clearTimeout);clearTimeout(at)}
}

export default function OvPaySheet() {
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
          "--c1": "#111111",
          "--c2": "#30d158",
          "--s": "16px",
          "--k": "1",
          "--fg": "#1d1d1f",
          "--bg": "#f5f5f7",
        } as CSSProperties}
      >
        <div className="uvq13-pay"><div className="pr"><i className="im" aria-hidden="true"></i><div><p className="nm">Brisa headphones</p><p className="pc">49,90 €</p></div><button type="button" className="fire" aria-haspopup="dialog">Buy</button></div><div className="ln" aria-hidden="true"><i></i><i></i><i className="s"></i></div><i className="side" aria-hidden="true"></i><div className="scr"></div><div className="sh" role="dialog" aria-modal="true" aria-labelledby="uvq13-pay-t" data-st="idle"><header><b id="uvq13-pay-t">Checkout</b><button type="button" className="x" aria-label="Cancel"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg></button></header><div className="mid"><div className="cc" aria-hidden="true"><i className="chip"></i><span>•••• 4242</span></div><dl className="rows"><div><dt>Card</dt><dd>Debit •••• 4242</dd></div><div><dt>Shipping</dt><dd>Calle Mayor 12, Madrid</dd></div><div className="tt"><dt>Total</dt><dd>49,90 €</dd></div></dl></div><div className="cf"><span className="bio" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path className="br" d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" /><path className="fc" d="M9 9.5v1.2M15 9.5v1.2M12 9.5v3.5h-1M9.5 15.5c1.4 1.1 3.6 1.1 5 0" /><circle className="ci" cx="12" cy="12" r="9" pathLength="1" /><path className="ck" d="M7.5 12.5l3 3 6-6.5" pathLength="1" /></svg></span><p className="msg" aria-live="polite"><span className="m0">Press twice to pay</span><span className="m1">Press again</span><span className="m2">Checking…</span><span className="m3">Done</span></p><i className="ar" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h13M13 6.5l5.5 5.5-5.5 5.5" /></svg></i></div><button type="button" className="go">Confirm payment</button></div><p className="tst" aria-live="polite"><span>Order confirmed</span></p></div>
      </div>
    </>
  );
}
