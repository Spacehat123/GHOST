document.querySelectorAll(".uvq13-pay").forEach(el => {
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
});
