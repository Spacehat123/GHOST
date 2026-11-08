interface GradientBorderButtonProps {
  color?: string;
  radius?: number;
  text?: string;
  /** Segundos por vuelta del borde. */
  intensity?: number;
}

export default function GradientBorderButton({
  color = "#0071e3",
  radius = 24,
  text = "Glow button",
  intensity = 3,
}: GradientBorderButtonProps) {
  return (
    <div
      className="relative overflow-hidden p-[2px]"
      style={{ borderRadius: radius + 2 }}
    >
      <div
        className="absolute inset-[-100%] animate-spin"
        style={{
          background: `conic-gradient(from 0deg, transparent 0%, ${color} 20%, transparent 40%, transparent 60%, ${color} 80%, transparent 100%)`,
          animationDuration: `${intensity}s`,
        }}
      />
      <button
        type="button"
        className="relative z-10 bg-[#f5f5f7] px-6 py-3 text-sm font-semibold text-[#1d1d1f] dark:bg-black dark:text-[#f5f5f7]"
        style={{ borderRadius: radius }}
      >
        {text}
      </button>
    </div>
  );
}
