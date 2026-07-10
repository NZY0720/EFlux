/**
 * Enables the SVG-refraction layer only where Chromium can render it reliably.
 * This is a web approximation of Liquid Glass, not an Apple platform material.
 */
type NavigatorWithUserAgentData = Navigator & {
  userAgentData?: { brands?: Array<{ brand: string }> };
};

const reducedTransparencyQuery = "(prefers-reduced-transparency: reduce)";

function supportsChromiumRefraction() {
  const brands = (navigator as NavigatorWithUserAgentData).userAgentData?.brands ?? [];
  return brands.some(({ brand }) => /Chromium|Google Chrome|Microsoft Edge|Opera/i.test(brand));
}

export function initializeLiquidGlass() {
  const root = document.documentElement;
  const transparency = window.matchMedia(reducedTransparencyQuery);
  const update = () => {
    root.dataset.lg = supportsChromiumRefraction() && !transparency.matches ? "true" : "false";
  };

  update();
  transparency.addEventListener?.("change", update);
}
