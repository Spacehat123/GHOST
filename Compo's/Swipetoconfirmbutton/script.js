document.querySelectorAll(".uv-swp").forEach(el => {
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
});
