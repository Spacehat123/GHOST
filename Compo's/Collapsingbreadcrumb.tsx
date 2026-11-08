import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Box, Folder, House, Layers, Palette } from "lucide-react";
import type { LucideIcon } from "lucide-react";

const CRUMB_PATH: { label: string; icon: LucideIcon }[] = [
  { label: "Home", icon: House },
  { label: "Projects", icon: Folder },
  { label: "Aimran", icon: Box },
  { label: "Design", icon: Palette },
  { label: "Navigation", icon: Layers },
];

const CRUMB_SPRING = { type: "spring", stiffness: 460, damping: 36 } as const;

interface CollapsingBreadcrumbProps {
  visible?: number;
  accent?: string;
}

export default function CollapsingBreadcrumb({
  visible = 0,
  accent = "#0070f3",
}: CollapsingBreadcrumbProps) {
  const [depth, setDepth] = useState(CRUMB_PATH.length - 1);
  const [peek, setPeek] = useState<number | null>(null);

  return (
    <nav aria-label="Breadcrumb" className="max-w-full">
      <motion.ol
        layout
        transition={CRUMB_SPRING}
        onPointerLeave={() => setPeek(null)}
        className="flex h-12 max-w-full items-center rounded-full bg-white px-1.5 text-[13px] text-[#1d1d1f] shadow-[0_1px_2px_rgba(0,0,0,0.04),0_10px_32px_rgba(0,0,0,0.07)] ring-1 ring-black/[0.06] dark:bg-[#1c1c1e] dark:text-[#f5f5f7] dark:shadow-[0_10px_32px_rgba(0,0,0,0.4)] dark:ring-white/10"
      >
        {CRUMB_PATH.map((crumb, i) => {
          const Icon = crumb.icon;
          const current = i === depth;
          const ahead = i > depth;
          const named = current || (i < depth && i >= depth - visible) || peek === i;
          return (
            <motion.li
              key={crumb.label}
              layout
              transition={CRUMB_SPRING}
              className="flex min-w-0 items-center"
            >
              {i > 0 && (
                <span
                  aria-hidden
                  className="select-none px-0.5 text-[16px] font-light text-black/15 dark:text-white/20"
                >
                  /
                </span>
              )}
              <button
                type="button"
                onClick={() => setDepth(i)}
                onPointerEnter={() => setPeek(i)}
                onFocus={() => setPeek(i)}
                onBlur={() => setPeek(null)}
                aria-current={current ? "page" : undefined}
                aria-label={ahead ? "Back to " + crumb.label : crumb.label}
                className={
                  "group flex h-9 min-w-9 items-center justify-center rounded-full px-2 outline-none transition-[background-color,color,opacity] duration-200 focus-visible:ring-2 active:scale-[0.96] " +
                  (current
                    ? "font-medium"
                    : "text-[#86868b] hover:bg-black/[0.04] hover:text-[#1d1d1f] dark:hover:bg-white/[0.07] dark:hover:text-[#f5f5f7]") +
                  (ahead ? " opacity-40 hover:opacity-100 focus-visible:opacity-100" : "")
                }
                style={{ ["--tw-ring-color" as string]: accent + "66" }}
              >
                <Icon
                  className="h-4 w-4 shrink-0"
                  strokeWidth={1.5}
                  style={{ color: current ? accent : undefined }}
                />
                <AnimatePresence initial={false}>
                  {named && (
                    <motion.span
                      key="label"
                      initial={{ width: 0, opacity: 0, filter: "blur(3px)" }}
                      animate={{ width: "auto", opacity: 1, filter: "blur(0px)" }}
                      exit={{ width: 0, opacity: 0, filter: "blur(3px)" }}
                      transition={CRUMB_SPRING}
                      className="block overflow-hidden whitespace-nowrap"
                    >
                      <span className="block max-w-[8.5rem] truncate pl-1.5">{crumb.label}</span>
                    </motion.span>
                  )}
                </AnimatePresence>
              </button>
            </motion.li>
          );
        })}
      </motion.ol>
    </nav>
  );
}
