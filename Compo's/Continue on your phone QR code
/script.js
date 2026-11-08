document.querySelectorAll(".uvq13-qr").forEach(el => {
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
});
