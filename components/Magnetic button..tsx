import { motion, useMotionValue, useSpring } from "framer-motion";

interface MagneticButtonProps {
  color?: string;
  radius?: number;
  text?: string;
  intensity?: number;
}

export default function MagneticButton({
  color = "#0071e3",
  radius = 24,
  text = "Hover me",
  intensity = 0.5,
}: MagneticButtonProps) {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const sx = useSpring(x, { stiffness: 200, damping: 15 });
  const sy = useSpring(y, { stiffness: 200, damping: 15 });

  return (
    <motion.button
      type="button"
      style={{ x: sx, y: sy, backgroundColor: color, borderRadius: radius }}
      className="px-6 py-3 text-sm font-semibold text-white shadow-lg"
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        x.set((e.clientX - r.left - r.width / 2) * intensity);
        y.set((e.clientY - r.top - r.height / 2) * intensity);
      }}
      onMouseLeave={() => {
        x.set(0);
        y.set(0);
      }}
    >
      {text}
    </motion.button>
  );
}
