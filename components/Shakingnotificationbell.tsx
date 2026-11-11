import { useEffect, useState } from "react";
import {
  AnimatePresence,
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "framer-motion";

const BELL_BODY =
  "M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326";
const BELL_CLAPPER = "M10.268 21a2 2 0 0 0 3.464 0";

interface BellShakeProps {
  color?: string;
  start?: number;
}

export default function BellShake({ color = "#ff453a", start = 3 }: BellShakeProps) {
  const [count, setCount] = useState(start);
  const [rings, setRings] = useState(0);
  const reduce = useReducedMotion();
  const rot = useMotionValue(0);
  const clapX = useSpring(
    useTransform(rot, (r) => r * -0.12),
    { stiffness: 260, damping: 7 },
  );
  const bump = useMotionValue(1);

  useEffect(() => {
    setCount(start);
  }, [start]);

  const ring = () => {
    setCount((c) => c + 1);
    setRings((r) => r + 1);
    if (reduce) return;
    animate(rot, 0, { type: "spring", velocity: -620, stiffness: 520, damping: 6 });
    animate(bump, 1, { type: "spring", velocity: 5, stiffness: 600, damping: 12 });
  };

  return (
    <motion.button
      type="button"
      onClick={ring}
      aria-label={"Notifications: " + count + " unread"}
      whileHover={{ scale: 1.04 }}
      whileTap={{ scale: 0.92 }}
      transition={{ type: "spring", stiffness: 500, damping: 22 }}
      className="relative grid h-14 w-14 place-items-center rounded-full bg-white text-[#1d1d1f] shadow-[0_0_0_1px_rgba(0,0,0,0.05),0_1px_2px_rgba(0,0,0,0.06),0_10px_28px_-6px_rgba(0,0,0,0.14)] outline-none focus-visible:ring-2 focus-visible:ring-black/20 dark:bg-[#2c2c2e] dark:text-[#f5f5f7] dark:shadow-[0_0_0_1px_rgba(255,255,255,0.08),0_10px_28px_-6px_rgba(0,0,0,0.6)] dark:focus-visible:ring-white/30"
    >
      <span className="relative h-6 w-6">
        <motion.svg
          viewBox="0 0 24 24"
          className="absolute inset-0 h-6 w-6"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ rotate: rot, originY: 0.08 }}
          aria-hidden="true"
        >
          <path d={BELL_BODY} />
          <motion.path d={BELL_CLAPPER} style={{ x: clapX }} />
        </motion.svg>
        {rings > 0 && !reduce && (
          <svg
            key={rings}
            viewBox="0 0 24 24"
            className="pointer-events-none absolute inset-0 h-6 w-6 overflow-visible"
            fill="none"
            stroke={color}
            strokeWidth={1.5}
            strokeLinecap="round"
            aria-hidden="true"
          >
            <motion.path
              d="M2.6 4.2C1.4 5.6 0.8 7.2 0.8 9"
              initial={{ opacity: 0, x: 1 }}
              animate={{ opacity: [0, 1, 0], x: -2.5 }}
              transition={{ duration: 0.55, ease: "easeOut" }}
            />
            <motion.path
              d="M21.4 4.2C22.6 5.6 23.2 7.2 23.2 9"
              initial={{ opacity: 0, x: -1 }}
              animate={{ opacity: [0, 1, 0], x: 2.5 }}
              transition={{ duration: 0.55, ease: "easeOut" }}
            />
          </svg>
        )}
      </span>
      <AnimatePresence>
        {count > 0 && (
          <motion.span
            className="absolute -right-0.5 -top-0.5"
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            exit={{ scale: 0 }}
            transition={{ type: "spring", stiffness: 500, damping: 20 }}
          >
            <motion.span
              className="flex h-5 min-w-5 items-center justify-center overflow-hidden rounded-full px-1.5 text-[11px] font-semibold tabular-nums text-white ring-[2.5px] ring-[#fafafa] dark:ring-[#161617]"
              style={{ backgroundColor: color, scale: bump }}
            >
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.span
                  key={count}
                  initial={{ y: 12, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ y: -12, opacity: 0 }}
                  transition={{ type: "spring", stiffness: 600, damping: 30 }}
                >
                  {count > 99 ? "99+" : count}
                </motion.span>
              </AnimatePresence>
            </motion.span>
          </motion.span>
        )}
      </AnimatePresence>
    </motion.button>
  );
}
