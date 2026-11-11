import { useState } from "react";
import { motion } from "framer-motion";
import { Star } from "lucide-react";

const spring = { type: "spring" as const, stiffness: 300, damping: 30 };

interface StarRatingProps {
  color?: string;
}

export default function StarRating({ color = "#c58b32" }: StarRatingProps) {
  const [rating, setRating] = useState(0);
  const [hover, setHover] = useState(0);

  return (
    <div className="flex gap-1">
      {[1, 2, 3, 4, 5].map((i) => (
        <motion.button
          key={i}
          type="button"
          aria-label={`${i} de 5 estrellas`}
          aria-pressed={rating === i}
          onMouseEnter={() => setHover(i)}
          onMouseLeave={() => setHover(0)}
          onClick={() => setRating(i)}
          whileHover={{ scale: 1.18, y: -2 }}
          transition={spring}
        >
          <Star
            className="h-8 w-8"
            stroke={color}
            fill={i <= (hover || rating) ? color : "transparent"}
          />
        </motion.button>
      ))}
    </div>
  );
}
