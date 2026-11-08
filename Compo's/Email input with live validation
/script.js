document.querySelectorAll(".uv-in2v").forEach(el => {
  const i=el.querySelector("input");const ck=()=>{const v=i.value.trim();el.classList.remove("ok","bad");if(!v)return;el.classList.add(/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(v)?"ok":"bad")};i.addEventListener("input",()=>{if(el.classList.contains("bad"))el.classList.remove("bad")});i.addEventListener("blur",ck);i.addEventListener("keydown",e=>{if(e.key==="Enter")ck()})
});
