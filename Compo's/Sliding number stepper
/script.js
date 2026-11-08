document.querySelectorAll(".uv-in2q").forEach(el => {
  let n=1;const b=el.querySelector(".nm b"),mn=el.querySelector(".mn");const set=(v,up)=>{if(v<1||v>99)return;n=v;b.textContent=n;b.className=up?"up":"dn";mn.disabled=n<=1};el.querySelector(".pl").addEventListener("click",()=>set(n+1,true));mn.addEventListener("click",()=>set(n-1,false));set(1,true)
});
