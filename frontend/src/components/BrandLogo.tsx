/**
 * Animated EFlux brand mark — the "flux bolt": a lightning bolt split into two
 * counterposed flow strokes (energy flowing out ↗ / flowing in ↙) separated by
 * a narrow flux channel, set inside an open orbit whose gap lets the strike
 * escape. A live current sweeps the orbit and the two strokes breathe in
 * alternating phase — the exchange rhythm of a P2P market. Pure self-contained
 * SVG (SMIL + gradients), so it animates anywhere inline SVG renders, no extra
 * deps and crisp at any size.
 */
interface Props {
  size?: number;
  className?: string;
  /** Pause the sweep/glow animation (e.g. respect reduced-motion contexts). */
  still?: boolean;
}

/** Upper flow stroke — sky, selling side of the strike. */
const BOLT_UP = "M26.3 7.3 L14.4 25.6 L22 25.6 L25.2 20.2 L28.5 7.3 Z";
/** Lower flow stroke — emerald, buying side of the strike. */
const BOLT_DOWN = "M23.8 26.7 L21.7 40.8 L34.7 21.3 L27.1 21.3 Z";

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
        <linearGradient id="eflux-up" x1="15" y1="8" x2="27" y2="26" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#7dd3fc" />
          <stop offset="1" stopColor="#38bdf8" />
        </linearGradient>
        <linearGradient id="eflux-down" x1="31" y1="21" x2="22" y2="40" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#2dd4bf" />
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

      {/* open orbit — the gap sits lower-left, where the strike exits */}
      <circle
        cx="24"
        cy="24"
        r="20"
        pathLength={100}
        stroke="#1e293b"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeDasharray="75 25"
        transform="rotate(180 24 24)"
      />

      {/* travelling current — a short bright arc chasing around the orbit */}
      <circle
        cx="24"
        cy="24"
        r="20"
        pathLength={100}
        stroke="#38bdf8"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeDasharray="16 84"
        transform="rotate(180 24 24)"
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

      {/* glow copies of both strokes, breathing in alternating phase */}
      <path d={BOLT_UP} fill="url(#eflux-up)" filter="url(#eflux-glow)" opacity="0.85">
        {!still && (
          <animate
            attributeName="opacity"
            values="0.35;0.95;0.35"
            dur="2.6s"
            repeatCount="indefinite"
          />
        )}
      </path>
      <path d={BOLT_DOWN} fill="url(#eflux-down)" filter="url(#eflux-glow)" opacity="0.85">
        {!still && (
          <animate
            attributeName="opacity"
            values="0.95;0.35;0.95"
            dur="2.6s"
            repeatCount="indefinite"
          />
        )}
      </path>

      {/* crisp strokes */}
      <path d={BOLT_UP} fill="url(#eflux-up)" />
      <path d={BOLT_DOWN} fill="url(#eflux-down)" />
    </svg>
  );
}
