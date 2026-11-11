// @ts-nocheck: setup() es JavaScript del DOM sin tipos, el mismo de la pestaña JS.
import { useEffect, useRef, type CSSProperties } from "react";

const css = `
.uvq13-qr {
  font-family: -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif;
  font-size: var(--s);
  color: var(--fg);
  -webkit-tap-highlight-color: transparent;
  position: relative;
  width: 22em;
  height: 20em;
  border-radius: 1.2em;
  overflow: hidden;
  background: color-mix(in srgb,var(--fg) 3%,var(--bg));
  box-shadow: 0 0 0 1px color-mix(in srgb,var(--fg) 9%,transparent);
}

.uvq13-qr .pg {
  position: absolute;
  inset: 0;
  padding: 1.2em;
  display: grid;
  align-content: start;
  gap: .55em;
}

.uvq13-qr .pg i {
  height: .6em;
  border-radius: 1em;
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uvq13-qr .pg .h {
  width: 45%;
  height: 1em;
  margin-bottom: .4em;
  background: color-mix(in srgb,var(--fg) 12%,transparent);
}

.uvq13-qr .pg .s {
  width: 60%;
}

.uvq13-qr .scr {
  position: absolute;
  inset: 0;
  z-index: 4;
  background: rgba(10,12,20,.38);
  opacity: 0;
  pointer-events: none;
  transition: opacity calc(0.3s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-qr.open .scr {
  opacity: 1;
  pointer-events: auto;
}

.uvq13-qr .fire {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  position: absolute;
  right: 1em;
  top: 1em;
  z-index: 1;
  padding: .55em .95em;
  border-radius: 2em;
  font-size: .78em;
  font-weight: 650;
  color: var(--bg);
  background: var(--fg);
  transition: transform calc(0.2s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-qr .fire:active {
  transform: scale(.96);
}

.uvq13-qr button:focus-visible,.uvq13-qr a:focus-visible,.uvq13-qr input:focus-visible {
  outline: 2px solid var(--c1);
  outline-offset: 3px;
}

.uvq13-qr .dl {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 5;
  box-sizing: border-box;
  transform: translate(-50%,-46%) scale(.95);
  opacity: 0;
  pointer-events: none;
  transition: transform calc(0.35s / var(--k)) cubic-bezier(.22,1,.36,1),opacity calc(0.25s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-qr.open .dl {
  transform: translate(-50%,-50%);
  opacity: 1;
  pointer-events: auto;
}

.uvq13-qr .dl {
  width: 17em;
  padding: 1.1em;
  border-radius: 1.2em;
  background: color-mix(in srgb,var(--fg) 2%,var(--bg));
  box-shadow: 0 0 0 1px color-mix(in srgb,var(--fg) 9%,transparent),0 1.6em 3.2em -1.2em rgba(0,0,0,.5);
}

.uvq13-qr .x {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  position: absolute;
  right: .7em;
  top: .7em;
  display: grid;
  place-items: center;
  width: 1.9em;
  height: 1.9em;
  border-radius: 50%;
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uvq13-qr .x svg {
  width: .9em;
  height: .9em;
}

.uvq13-qr .ti {
  margin: 0;
  padding-right: 1.8em;
  font-size: 1em;
  font-weight: 800;
}

.uvq13-qr .sb {
  margin: .2em 0 .8em;
  font-size: .72em;
  color: color-mix(in srgb,var(--fg) 55%,transparent);
}

.uvq13-qr .bd {
  display: flex;
  align-items: center;
  gap: .9em;
}

.uvq13-qr .qr {
  position: relative;
  flex: none;
  width: 7.4em;
  height: 7.4em;
  border-radius: .6em;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 0 0 1px color-mix(in srgb,var(--fg) 9%,transparent);
}

.uvq13-qr .qr svg {
  display: block;
  width: 100%;
  height: 100%;
  transition: filter calc(0.4s / var(--k)) cubic-bezier(.25,.1,.25,1),opacity calc(0.4s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-qr .md {
  fill: var(--c1);
  transition: opacity calc(0.25s / var(--k)) cubic-bezier(.25,.1,.25,1);
}

.uvq13-qr .rg .md {
  opacity: 0;
}

.uvq13-qr .ls {
  position: absolute;
  left: 5%;
  right: 5%;
  top: 6%;
  height: 2px;
  border-radius: 2px;
  background: #ef4444;
  box-shadow: 0 0 .6em #ef4444,0 0 1.4em #ef4444;
  animation: uvq13-qr-l calc(2.2s / var(--k)) ease-in-out infinite alternate;
}

.uvq13-qr .done .ls {
  display: none;
}

.uvq13-qr .done .qr svg {
  filter: blur(3px);
  opacity: .45;
}

.uvq13-qr .ok {
  position: absolute;
  left: 50%;
  top: 50%;
  display: grid;
  place-items: center;
  width: 2.8em;
  height: 2.8em;
  margin: -1.4em;
  border-radius: 50%;
  color: #fff;
  background: var(--c2);
  transform: scale(0);
  transition: transform calc(0.45s / var(--k)) cubic-bezier(.34,1.56,.64,1);
}

.uvq13-qr .ok svg {
  width: 1.5em;
  height: 1.5em;
}

.uvq13-qr .done .ok {
  transform: none;
}

.uvq13-qr ol {
  display: grid;
  gap: .4em;
  margin: 0;
  padding: 0 0 0 1.2em;
  font-size: .74em;
  line-height: 1.35;
}

.uvq13-qr li::marker {
  font-weight: 800;
  color: var(--c2);
}

.uvq13-qr .cd {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: .3em;
  margin-top: .9em;
  font-size: .7em;
  color: color-mix(in srgb,var(--fg) 55%,transparent);
}

.uvq13-qr .cd .t {
  color: var(--fg);
  font-variant-numeric: tabular-nums;
}

.uvq13-qr .bar {
  flex-basis: 100%;
  height: .3em;
  border-radius: 1em;
  background: color-mix(in srgb,var(--fg) 6%,transparent);
  overflow: hidden;
}

.uvq13-qr .bar b {
  display: block;
  width: calc(var(--p,1) * 100%);
  height: 100%;
  background: var(--c1);
  transition: width 1s linear;
}

.uvq13-qr .ac {
  display: grid;
  grid-template-columns: 1fr 1.35fr;
  gap: .4em;
  margin-top: .8em;
}

.uvq13-qr .ac button {
  font: inherit;
  color: inherit;
  border: 0;
  margin: 0;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  padding: .65em .5em;
  border-radius: .7em;
  font-size: .74em;
  font-weight: 700;
  background: color-mix(in srgb,var(--fg) 6%,transparent);
}

.uvq13-qr .ac .sc {
  color: var(--bg);
  background: var(--fg);
}

.uvq13-qr .ac button:active {
  transform: scale(.97);
}

.uvq13-qr .dn {
  display: none;
  align-items: center;
  gap: .4em;
  margin: .9em 0 0;
  font-size: .82em;
  font-weight: 700;
  color: var(--c2);
}

.uvq13-qr .dn svg {
  width: 1.2em;
  height: 1.2em;
}

.uvq13-qr .done .dn {
  display: flex;
  animation: uvq13-qr-i calc(0.4s / var(--k)) cubic-bezier(.34,1.56,.64,1);
}

.uvq13-qr .done .ac,.uvq13-qr .done .cd {
  display: none;
}

@keyframes uvq13-qr-l {
  to {
    top: 93%;
  }
}

@keyframes uvq13-qr-i {
  from {
    opacity: 0;
    transform: translateY(.4em);
  }
}

@media (prefers-reduced-motion:reduce) {
  .uvq13-qr,.uvq13-qr *,.uvq13-qr *::before,.uvq13-qr *::after {
    animation-duration: .01ms!important;
    animation-iteration-count: 1!important;
    transition-duration: .01ms!important;
  }
}
`;

// Interacción en JavaScript del DOM: la misma que en la pestaña JS.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function setup(el: any) {
  const fire=el.querySelector(".fire"),scr=el.querySelector(".scr"),pnl=el.querySelector(".dl");let tms=[];const later=(f,ms)=>tms.push(setTimeout(f,ms));
const foc=(t,ms)=>later(()=>{const n=typeof t==="string"?el.querySelector(t):t;n&&n.focus({preventScroll:true})},ms||0);
const isOpen=()=>el.classList.contains("open");
const open=u=>{if(isOpen())return;el.classList.add("open");pnl.inert=false;fire&&fire.setAttribute("aria-expanded","true");reset();u&&foc('.ac .sc',300)};
const close=()=>{if(!isOpen())return;el.classList.remove("open");pnl.inert=true;fire&&fire.setAttribute("aria-expanded","false");clearInterval(iv);fire&&fire.focus({preventScroll:true})};
fire&&fire.addEventListener("click",()=>isOpen()?close():open(true));scr&&scr.addEventListener("click",()=>close());
el.addEventListener("keydown",e=>{if(!isOpen())return;if(e.key==="Escape"){e.preventDefault();e.stopPropagation();close()}else if(e.key==="Tab"){const f=[...pnl.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea,a[href],[tabindex="0"]')].filter(x=>x.getClientRects().length);if(!f.length){e.preventDefault();return}const a=f[0],z=f[f.length-1],ac=document.activeElement;if(e.shiftKey&&(ac===a||!pnl.contains(ac))){e.preventDefault();z.focus()}else if(!e.shiftKey&&(ac===z||!pnl.contains(ac))){e.preventDefault();a.focus()}}});
pnl.inert=true;later(()=>open(false),500);const dl=el.querySelector(".dl"),md=el.querySelector(".md"),t=el.querySelector(".cd .t"),bar=el.querySelector(".cd .bar");let left=30,iv=0;
const gen=()=>{const N=25;let d="";for(let y=0;y<N;y++)for(let x=0;x<N;x++){const fx=x<8?3:x>=N-8?N-4:-1,fy=y<8?3:y>=N-8?N-4:-1;let on;if(fx>=0&&fy>=0&&!(fx===N-4&&fy===N-4)){const m=Math.max(Math.abs(x-fx),Math.abs(y-fy));on=m===3||m<=1}else if(Math.abs(x-18)<=2&&Math.abs(y-18)<=2)on=Math.max(Math.abs(x-18),Math.abs(y-18))!==1;else if(x===6||y===6)on=(x+y)%2===0;else on=Math.random()<.48;if(on)d+="M"+x+" "+y+"h1v1h-1z"}return d};
const paint=()=>{t.textContent="0:"+String(left).padStart(2,"0");bar.style.setProperty("--p",String(left/30))};
const regen=()=>{dl.classList.add("rg");later(()=>{md.setAttribute("d",gen());dl.classList.remove("rg")},260);left=30;paint()};
const reset=()=>{clearInterval(iv);dl.classList.remove("done");left=30;paint();iv=setInterval(()=>{left--;if(left<=0)regen();else paint()},1000)};
el.querySelector(".rf").addEventListener("click",regen);
el.querySelector(".ac .sc").addEventListener("click",()=>{clearInterval(iv);dl.classList.add("done");foc(".dl .x",0);later(close,1900)});
el.querySelector(".dl .x").addEventListener("click",close);return()=>{tms.forEach(clearTimeout);clearInterval(iv)}
}

export default function OvQrHandoff() {
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
          "--c1": "#111827",
          "--c2": "#16a34a",
          "--s": "16px",
          "--k": "1",
          "--fg": "#1d1d1f",
          "--bg": "#f5f5f7",
        } as CSSProperties}
      >
        <div className="uvq13-qr"><div className="pg" aria-hidden="true"><i className="h"></i><i></i><i></i><i></i><i></i><i className="s"></i></div><button type="button" className="fire" aria-haspopup="dialog">Open on phone</button><div className="scr"></div><div className="dl" role="dialog" aria-modal="true" aria-labelledby="uvq13-qr-t"><button type="button" className="x" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg></button><p className="ti" id="uvq13-qr-t">Continue on your phone</p><p className="sb">Scan the code with your phone camera.</p><div className="bd"><div className="qr"><svg viewBox="-2 -2 29 29" role="img" aria-label="QR code"><rect x="-2" y="-2" width="29" height="29" rx="2" fill="#fff" /><path className="md" d="M0 0h1v1h-1zM1 0h1v1h-1zM2 0h1v1h-1zM3 0h1v1h-1zM4 0h1v1h-1zM5 0h1v1h-1zM6 0h1v1h-1zM8 0h1v1h-1zM10 0h1v1h-1zM11 0h1v1h-1zM14 0h1v1h-1zM18 0h1v1h-1zM19 0h1v1h-1zM20 0h1v1h-1zM21 0h1v1h-1zM22 0h1v1h-1zM23 0h1v1h-1zM24 0h1v1h-1zM0 1h1v1h-1zM6 1h1v1h-1zM12 1h1v1h-1zM13 1h1v1h-1zM16 1h1v1h-1zM18 1h1v1h-1zM24 1h1v1h-1zM0 2h1v1h-1zM2 2h1v1h-1zM3 2h1v1h-1zM4 2h1v1h-1zM6 2h1v1h-1zM9 2h1v1h-1zM12 2h1v1h-1zM18 2h1v1h-1zM20 2h1v1h-1zM21 2h1v1h-1zM22 2h1v1h-1zM24 2h1v1h-1zM0 3h1v1h-1zM2 3h1v1h-1zM3 3h1v1h-1zM4 3h1v1h-1zM6 3h1v1h-1zM11 3h1v1h-1zM12 3h1v1h-1zM14 3h1v1h-1zM15 3h1v1h-1zM16 3h1v1h-1zM18 3h1v1h-1zM20 3h1v1h-1zM21 3h1v1h-1zM22 3h1v1h-1zM24 3h1v1h-1zM0 4h1v1h-1zM2 4h1v1h-1zM3 4h1v1h-1zM4 4h1v1h-1zM6 4h1v1h-1zM8 4h1v1h-1zM9 4h1v1h-1zM14 4h1v1h-1zM15 4h1v1h-1zM18 4h1v1h-1zM20 4h1v1h-1zM21 4h1v1h-1zM22 4h1v1h-1zM24 4h1v1h-1zM0 5h1v1h-1zM6 5h1v1h-1zM9 5h1v1h-1zM10 5h1v1h-1zM12 5h1v1h-1zM13 5h1v1h-1zM14 5h1v1h-1zM18 5h1v1h-1zM24 5h1v1h-1zM0 6h1v1h-1zM1 6h1v1h-1zM2 6h1v1h-1zM3 6h1v1h-1zM4 6h1v1h-1zM5 6h1v1h-1zM6 6h1v1h-1zM8 6h1v1h-1zM10 6h1v1h-1zM12 6h1v1h-1zM14 6h1v1h-1zM16 6h1v1h-1zM18 6h1v1h-1zM19 6h1v1h-1zM20 6h1v1h-1zM21 6h1v1h-1zM22 6h1v1h-1zM23 6h1v1h-1zM24 6h1v1h-1zM9 7h1v1h-1zM10 7h1v1h-1zM11 7h1v1h-1zM12 7h1v1h-1zM13 7h1v1h-1zM14 7h1v1h-1zM0 8h1v1h-1zM2 8h1v1h-1zM6 8h1v1h-1zM11 8h1v1h-1zM13 8h1v1h-1zM15 8h1v1h-1zM16 8h1v1h-1zM17 8h1v1h-1zM20 8h1v1h-1zM22 8h1v1h-1zM23 8h1v1h-1zM24 8h1v1h-1zM0 9h1v1h-1zM2 9h1v1h-1zM4 9h1v1h-1zM5 9h1v1h-1zM8 9h1v1h-1zM15 9h1v1h-1zM16 9h1v1h-1zM17 9h1v1h-1zM18 9h1v1h-1zM19 9h1v1h-1zM21 9h1v1h-1zM0 10h1v1h-1zM2 10h1v1h-1zM3 10h1v1h-1zM5 10h1v1h-1zM6 10h1v1h-1zM8 10h1v1h-1zM12 10h1v1h-1zM14 10h1v1h-1zM18 10h1v1h-1zM19 10h1v1h-1zM20 10h1v1h-1zM22 10h1v1h-1zM2 11h1v1h-1zM3 11h1v1h-1zM5 11h1v1h-1zM9 11h1v1h-1zM15 11h1v1h-1zM16 11h1v1h-1zM19 11h1v1h-1zM20 11h1v1h-1zM21 11h1v1h-1zM24 11h1v1h-1zM2 12h1v1h-1zM4 12h1v1h-1zM5 12h1v1h-1zM6 12h1v1h-1zM7 12h1v1h-1zM13 12h1v1h-1zM15 12h1v1h-1zM21 12h1v1h-1zM22 12h1v1h-1zM23 12h1v1h-1zM4 13h1v1h-1zM7 13h1v1h-1zM8 13h1v1h-1zM10 13h1v1h-1zM12 13h1v1h-1zM13 13h1v1h-1zM15 13h1v1h-1zM16 13h1v1h-1zM23 13h1v1h-1zM3 14h1v1h-1zM4 14h1v1h-1zM5 14h1v1h-1zM6 14h1v1h-1zM7 14h1v1h-1zM8 14h1v1h-1zM9 14h1v1h-1zM12 14h1v1h-1zM13 14h1v1h-1zM14 14h1v1h-1zM16 14h1v1h-1zM17 14h1v1h-1zM19 14h1v1h-1zM22 14h1v1h-1zM23 14h1v1h-1zM24 14h1v1h-1zM0 15h1v1h-1zM3 15h1v1h-1zM7 15h1v1h-1zM8 15h1v1h-1zM10 15h1v1h-1zM11 15h1v1h-1zM12 15h1v1h-1zM17 15h1v1h-1zM18 15h1v1h-1zM19 15h1v1h-1zM20 15h1v1h-1zM21 15h1v1h-1zM22 15h1v1h-1zM23 15h1v1h-1zM24 15h1v1h-1zM3 16h1v1h-1zM6 16h1v1h-1zM10 16h1v1h-1zM11 16h1v1h-1zM16 16h1v1h-1zM17 16h1v1h-1zM18 16h1v1h-1zM19 16h1v1h-1zM20 16h1v1h-1zM10 17h1v1h-1zM14 17h1v1h-1zM15 17h1v1h-1zM16 17h1v1h-1zM20 17h1v1h-1zM21 17h1v1h-1zM0 18h1v1h-1zM1 18h1v1h-1zM2 18h1v1h-1zM3 18h1v1h-1zM4 18h1v1h-1zM5 18h1v1h-1zM6 18h1v1h-1zM10 18h1v1h-1zM11 18h1v1h-1zM13 18h1v1h-1zM16 18h1v1h-1zM18 18h1v1h-1zM20 18h1v1h-1zM24 18h1v1h-1zM0 19h1v1h-1zM6 19h1v1h-1zM11 19h1v1h-1zM13 19h1v1h-1zM14 19h1v1h-1zM15 19h1v1h-1zM16 19h1v1h-1zM20 19h1v1h-1zM22 19h1v1h-1zM0 20h1v1h-1zM2 20h1v1h-1zM3 20h1v1h-1zM4 20h1v1h-1zM6 20h1v1h-1zM8 20h1v1h-1zM11 20h1v1h-1zM12 20h1v1h-1zM14 20h1v1h-1zM15 20h1v1h-1zM16 20h1v1h-1zM17 20h1v1h-1zM18 20h1v1h-1zM19 20h1v1h-1zM20 20h1v1h-1zM21 20h1v1h-1zM22 20h1v1h-1zM0 21h1v1h-1zM2 21h1v1h-1zM3 21h1v1h-1zM4 21h1v1h-1zM6 21h1v1h-1zM8 21h1v1h-1zM9 21h1v1h-1zM12 21h1v1h-1zM13 21h1v1h-1zM15 21h1v1h-1zM19 21h1v1h-1zM20 21h1v1h-1zM0 22h1v1h-1zM2 22h1v1h-1zM3 22h1v1h-1zM4 22h1v1h-1zM6 22h1v1h-1zM12 22h1v1h-1zM13 22h1v1h-1zM15 22h1v1h-1zM17 22h1v1h-1zM18 22h1v1h-1zM22 22h1v1h-1zM24 22h1v1h-1zM0 23h1v1h-1zM6 23h1v1h-1zM9 23h1v1h-1zM13 23h1v1h-1zM17 23h1v1h-1zM23 23h1v1h-1zM0 24h1v1h-1zM1 24h1v1h-1zM2 24h1v1h-1zM3 24h1v1h-1zM4 24h1v1h-1zM5 24h1v1h-1zM6 24h1v1h-1zM8 24h1v1h-1zM9 24h1v1h-1zM12 24h1v1h-1zM13 24h1v1h-1zM14 24h1v1h-1zM18 24h1v1h-1zM19 24h1v1h-1zM20 24h1v1h-1zM21 24h1v1h-1zM23 24h1v1h-1zM24 24h1v1h-1z" /></svg><i className="ls" aria-hidden="true"></i><span className="ok" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg></span></div><ol><li>Open the camera</li><li>Point it at the code</li><li>Tap the link that appears</li></ol></div><div className="cd"><span>The code expires in</span> <b className="t">0:30</b><i className="bar"><b></b></i></div><div className="ac"><button type="button" className="rf">New code</button><button type="button" className="sc">I have scanned it</button></div><p className="dn" aria-live="polite"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="7" y="2.5" width="10" height="19" rx="2.5" /><path d="M11 18.5h2" /></svg><span>Connected to your phone</span></p></div></div>
      </div>
    </>
  );
}
