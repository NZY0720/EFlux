/**
 * Animated EFlux brand mark — a lightning bolt inside a ring with a live
 * electric "current" sweeping around it and a soft glow that pulses in time.
 * Pure self-contained SVG (SMIL + gradients), so it animates anywhere an
 * <img>/inline-SVG renders, no extra deps and crisp at any size.
 */
interface Props {
  size?: number;
  className?: string;
  /** Pause the sweep/glow animation (e.g. respect reduced-motion contexts). */
  still?: boolean;
}

const BOLT = "M27 9 L16 26 L23 26 L21 39 L33 21 L26 21 L29 9 Z";

export default function BrandLogo({ size = 30, className, still = false }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      className={className}
      role="img"
      aria-label="EFlux"
    >
      <defs>
        <linearGradient id="eflux-bolt" x1="14" y1="8" x2="34" y2="40" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#7dd3fc" />
          <stop offset="0.5" stopColor="#38bdf8" />
          <stop offset="1" stopColor="#34d399" />
        </linearGradient>
        <radialGradient id="eflux-core" cx="0.5" cy="0.42" r="0.62">
          <stop offset="0" stopColor="#0ea5e9" stopOpacity="0.35" />
          <stop offset="1" stopColor="#0ea5e9" stopOpacity="0" />
        </radialGradient>
        <filter id="eflux-glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="1.6" />
        </filter>
      </defs>

      {/* soft inner halo */}
      <circle cx="24" cy="24" r="19" fill="url(#eflux-core)" />

      {/* base ring */}
      <circle cx="24" cy="24" r="20" stroke="#1e293b" strokeWidth="2.5" />

      {/* travelling current — a short bright arc chasing around the ring */}
      <circle
        cx="24"
        cy="24"
        r="20"
        pathLength={100}
        stroke="#38bdf8"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeDasharray="16 84"
      >
        {!still && (
          <animate
            attributeName="stroke-dashoffset"
            from="0"
            to="-100"
            dur="2.6s"
            repeatCount="indefinite"
          />
        )}
      </circle>

      {/* glow copy of the bolt, breathing behind the sharp one */}
      <path d={BOLT} fill="url(#eflux-bolt)" filter="url(#eflux-glow)" opacity="0.85">
        {!still && (
          <animate
            attributeName="opacity"
            values="0.35;0.95;0.35"
            dur="2.6s"
            repeatCount="indefinite"
          />
        )}
      </path>

      {/* crisp bolt */}
      <path d={BOLT} fill="url(#eflux-bolt)" />
    </svg>
  );
}
