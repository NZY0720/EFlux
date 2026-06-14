/**
 * EFlux icon set — hand-drawn, energy-themed line icons.
 *
 * All icons share a 24×24 viewBox, round line joins and `currentColor` so they
 * tint from the surrounding text color (or an explicit `color`/style). Pass
 * `size` for square dimensions and any standard SVG props through `...rest`.
 */
import type { SVGProps } from "react";

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "color"> {
  size?: number | string;
}

function Svg({ size = 18, children, strokeWidth = 1.7, ...rest }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth as number}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

/** Sun — rooftop solar / PV. */
export function SolarIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2.2M12 19.3v2.2M4.2 4.2l1.6 1.6M18.2 18.2l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.2 19.8l1.6-1.6M18.2 5.8l1.6-1.6" />
    </Svg>
  );
}

/** Wind turbine — wind farms. Three swept blades on a tower. */
export function WindIcon(props: IconProps) {
  const blade = "M12 11 Q10.7 6.3 12 2.4 Q13.3 6.3 12 11 Z";
  return (
    <Svg {...props}>
      <g fill="currentColor" stroke="none">
        <path d={blade} />
        <path d={blade} transform="rotate(120 12 11)" />
        <path d={blade} transform="rotate(240 12 11)" />
        <circle cx="12" cy="11" r="1.5" />
      </g>
      <path d="M12 11.5v9M9.6 20.5h4.8" />
    </Svg>
  );
}

/** Battery with a charge bolt — storage / flexible load. */
export function BatteryIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="7" width="15" height="10" rx="2" />
      <path d="M21 10.5v3" />
      <path d="M11 9.3 8.2 12.6h2.6L9.4 15.2l3.4-3.6h-2.6z" fill="currentColor" stroke="none" />
    </Svg>
  );
}

/** Flame — gas peaker plants. */
export function GasIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3c2.5 3 4.8 5.2 4.8 9a4.8 4.8 0 0 1-9.6 0c0-1.7.7-3 1.7-4.2.3 1 .9 1.7 1.8 2C11 7.8 10.7 5.5 12 3Z" />
      <path d="M12 20.5a2.4 2.4 0 0 0 2.4-2.6c0-1.3-1.2-2.1-2.4-3.4-1.2 1.3-2.4 2.1-2.4 3.4A2.4 2.4 0 0 0 12 20.5Z" />
    </Svg>
  );
}

/** AI sparkles — the LLM-steered agents. */
export function LlmIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3.5c.5 3.1 1.9 4.5 5 5-3.1.5-4.5 1.9-5 5-.5-3.1-1.9-4.5-5-5 3.1-.5 4.5-1.9 5-5Z" />
      <path d="M18.5 13.5c.3 1.5.9 2.1 2.5 2.5-1.6.4-2.2 1-2.5 2.5-.3-1.5-.9-2.1-2.5-2.5 1.6-.4 2.2-1 2.5-2.5Z" />
    </Svg>
  );
}

/** Person — external / human-submitted orders. */
export function ExternalIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="8" r="3.3" />
      <path d="M5.5 20c.6-3.4 3.2-5.3 6.5-5.3s5.9 1.9 6.5 5.3" />
    </Svg>
  );
}

/** Bar-chart / market overview. */
export function MarketIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 20h16" />
      <path d="M7 20v-6M12 20V6M17 20v-9" />
    </Svg>
  );
}

/** People — participants roster. */
export function ParticipantsIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="9" cy="8.5" r="2.8" />
      <path d="M3.5 19c.5-2.9 2.7-4.6 5.5-4.6 1 0 1.9.2 2.7.6" />
      <circle cx="16.5" cy="9.5" r="2.3" />
      <path d="M14 14.7c2.4.2 4.1 1.7 4.5 4.3" />
    </Svg>
  );
}

/** Stacked panels — My VPPs. */
export function VppIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3 21 7.5 12 12 3 7.5 12 3Z" />
      <path d="M3 12.5 12 17l9-4.5M3 16.8 12 21.3l9-4.5" />
    </Svg>
  );
}

/** Lightning bolt — last price / energy. */
export function BoltIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13 2 5 13h6l-1 9 8-11h-6l1-9Z" />
    </Svg>
  );
}

/** Balance scale — supply vs demand. */
export function ScaleIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3v18M7 21h10" />
      <path d="M5 7h14M8.5 6.5 5 13h7zM15.5 6.5 12 13h7z" />
      <path d="M3 13a4 2 0 0 0 8 0M13 13a4 2 0 0 0 8 0" />
    </Svg>
  );
}

/** Speed gauge — sim clock. */
export function GaugeIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 18a8 8 0 1 1 16 0" />
      <path d="m12 14 4-4" />
      <circle cx="12" cy="14" r="1.1" fill="currentColor" stroke="none" />
    </Svg>
  );
}

/** Down-right trend — best bid. */
export function TrendDownIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 7l7 7 3-3 6 6" />
      <path d="M16 17h4v-4" />
    </Svg>
  );
}

/** Up-right trend — best ask. */
export function TrendUpIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 17l7-7 3 3 6-6" />
      <path d="M16 7h4v4" />
    </Svg>
  );
}

/** Map of merit-order category → icon. */
export function CategoryIcon({ category, ...props }: IconProps & { category: string }) {
  switch (category) {
    case "solar":
      return <SolarIcon {...props} />;
    case "wind":
      return <WindIcon {...props} />;
    case "battery_load":
      return <BatteryIcon {...props} />;
    case "llm":
      return <LlmIcon {...props} />;
    case "gas":
      return <GasIcon {...props} />;
    default:
      return <ExternalIcon {...props} />;
  }
}
