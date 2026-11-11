import type { CSSProperties } from "react";

const css = `
.ul-orb {
  position: relative;
  width: var(--s);
  height: var(--s);
}

.ul-orb i {
  position: absolute;
  border-radius: 50%;
  border: calc(var(--s) / 18) solid transparent;
  border-top-color: var(--c1);
  animation: ul-orb calc(1.2s / var(--k)) cubic-bezier(.65,0,.35,1) infinite;
}

.ul-orb i:nth-child(1) {
  inset: 0;
}

.ul-orb i:nth-child(2) {
  inset: 18%;
  border-top-color: var(--c2);
  animation-duration: calc(1.6s / var(--k));
  animation-direction: reverse;
}

.ul-orb i:nth-child(3) {
  inset: 36%;
  animation-duration: calc(0.9s / var(--k));
}

@keyframes ul-orb {
  to {
    transform: rotate(360deg);
  }
}
`;

export default function LoaderOrbits() {
  return (
    <>
      <style>{css}</style>
      <div
        style={{
          "--c1": "#0071e3",
          "--c2": "#8ec5ff",
          "--s": "72px",
          "--k": "1",
          "--bg": "#f5f5f7",
        } as CSSProperties}
      >
        <div className="ul-orb"><i></i><i></i><i></i></div>
      </div>
    </>
  );
}
