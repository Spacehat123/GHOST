import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Loader2 } from "lucide-react";

interface LoadingButtonProps {
  color?: string;
  radius?: number;
  text?: string;
  /** Multiplica la velocidad de la carga simulada. */
  intensity?: number;
}

export default function LoadingButton({
  color = "#0071e3",
  radius = 24,
  text = "Save changes",
  intensity = 1,
}: LoadingButtonProps) {
  const [state, setState] = useState<"idle" | "loading" | "success">("idle");

  return (
    <motion.button
      type="button"
      className="flex min-w-36 items-center justify-center gap-2 px-6 py-3 text-sm font-semibold text-white shadow-lg"
      style={{
        backgroundColor: state === "success" ? "#0071e3" : color,
        borderRadius: radius,
      }}
      animate={{ scale: state === "loading" ? 0.97 : 1 }}
      onClick={() => {
        if (state !== "idle") return;
        setState("loading");
        setTimeout(() => setState("success"), 1500 / intensity);
        setTimeout(() => setState("idle"), 1500 / intensity + 1800);
      }}
    >
      <AnimatePresence mode="wait" initial={false}>
        {state === "idle" && (
          <motion.span
            key="idle"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {text}
          </motion.span>
        )}
        {state === "loading" && (
          <motion.span
            key="loading"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <Loader2 className="h-4 w-4 animate-spin" />
          </motion.span>
        )}
        {state === "success" && (
          <motion.span
            key="success"
            initial={{ opacity: 0, scale: 0.5 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0 }}
            className="flex items-center gap-1.5"
          >
            <Check className="h-4 w-4" /> Done
          </motion.span>
        )}
      </AnimatePresence>
    </motion.button>
  );
}
