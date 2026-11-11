import { useEffect, useId, useRef, useState } from "react";
import { AnimatePresence, animate, motion, useMotionValue, useReducedMotion, useTransform } from "framer-motion";

interface BookmarkSaveProps {
  color?: string;
  savedLabel?: string;
}

export default function BookmarkSave({
  color = "#ffb800",
  savedLabel = "Saved",
}: BookmarkSaveProps) {
  const [saved, setSaved] = useState(false);
  const [chip, setChip] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const clipId = "bm" + useId().replace(/[^a-zA-Z0-9]/g, "");
  const pull = useMotionValue(0);
  const reduce = useReducedMotion();
  const ribbon = useTransform(pull, (p) => {
    const tail = 21 + p * 3;
    const notch = 16.5 + p * 1.5;
    return "M5 5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2V" + tail + "L12 " + notch + "L5 " + tail + "Z";
  });

  useEffect(() => () => clearTimeout(timer.current), []);

  const toggle = () => {
    const next = !saved;
    setSaved(next);
    setChip(next);
    clearTimeout(timer.current);
    if (next) timer.current = setTimeout(() => setChip(false), 1400);
    if (reduce) return;
    animate(pull, 0, { type: "spring", velocity: next ? 11 : -6, stiffness: 420, damping: 9 });
  };

  return (
    <div className="relative">
      <AnimatePresence>
        {chip && (
          <motion.span
            className="pointer-events-none absolute bottom-full left-1/2 mb-2 -translate-x-1/2 whitespace-nowrap"
            initial={{ opacity: 0, y: 6, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -4, filter: "blur(4px)" }}
            transition={{ type: "spring", stiffness: 500, damping: 30 }}
          >
            <span className="block rounded-full bg-[#1d1d1f] px-2.5 py-1 text-xs font-medium text-white shadow-[0_4px_12px_rgba(0,0,0,0.15)] dark:bg-white dark:text-[#1d1d1f]">
              {savedLabel}
            </span>
          </motion.span>
        )}
      </AnimatePresence>
      <motion.button
        type="button"
        onClick={toggle}
        aria-pressed={saved}
        aria-label={saved ? "Remove from saved" : "Save"}
        whileTap={{ scale: 0.86 }}
        transition={{ type: "spring", stiffness: 600, damping: 24 }}
        className="grid h-14 w-14 place-items-center rounded-full text-[#1d1d1f] outline-none transition-colors hover:bg-black/[0.04] focus-visible:ring-2 focus-visible:ring-black/20 dark:text-[#f5f5f7] dark:hover:bg-white/[0.06] dark:focus-visible:ring-white/30"
      >
        <svg viewBox="0 0 24 24" className="h-8 w-8 overflow-visible" aria-hidden="true">
          <defs>
            <clipPath id={clipId}>
              <motion.path d={ribbon} />
            </clipPath>
          </defs>
          <g clipPath={"url(#" + clipId + ")"}>
            <motion.rect
              x="0"
              width="24"
              height="28"
              fill={color}
              initial={false}
              animate={{ y: saved ? -1 : -29 }}
              transition={saved ? { type: "spring", stiffness: 160, damping: 18 } : { duration: 0.22, ease: [0.4, 0, 1, 1] }}
            />
          </g>
          <motion.path
            d={ribbon}
            fill="none"
            strokeWidth={1.5}
            strokeLinejoin="round"
            initial={false}
            animate={{ stroke: saved ? color : "currentColor" }}
            transition={{ duration: 0.2 }}
          />
        </svg>
      </motion.button>
    </div>
  );
}
