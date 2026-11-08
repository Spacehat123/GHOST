import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, Check } from "lucide-react";

const spring = { type: "spring" as const, stiffness: 300, damping: 30 };

interface DynamicIslandToastProps {
  width?: number;
}

export default function DynamicIslandToast({ width = 300 }: DynamicIslandToastProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative h-40 w-full">
      <motion.button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        animate={{
          width: open ? width : 130,
          height: open ? 82 : 38,
          borderRadius: open ? 24 : 22,
        }}
        transition={spring}
        className="absolute left-1/2 top-0 -translate-x-1/2 overflow-hidden bg-[#1d1d1f] text-[#f5f5f7] shadow-[0_4px_12px_rgba(0,0,0,0.04),0_24px_60px_rgba(0,0,0,0.1)]"
      >
        <AnimatePresence mode="wait">
          {open ? (
            <motion.span
              key="open"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex items-center gap-3 px-5 text-left"
            >
              <span className="grid h-10 w-10 place-items-center rounded-full bg-[#34c759]">
                <Check className="h-5 w-5" />
              </span>
              <span>
                <b className="block text-sm">Booking confirmed</b>
                <small className="text-[#f5f5f7]/60">You’re all set</small>
              </span>
            </motion.span>
          ) : (
            <motion.span
              key="closed"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex items-center justify-center gap-2 text-xs"
            >
              <Bell className="h-3.5 w-3.5" /> New alert
            </motion.span>
          )}
        </AnimatePresence>
      </motion.button>
    </div>
  );
}
