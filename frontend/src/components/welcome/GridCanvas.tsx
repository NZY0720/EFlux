import {
  forwardRef,
  memo,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";

import { CATEGORY_META } from "../../lib/categories";

/**
 * The welcome page's living grid: a stylized transmission network rendered on
 * canvas. Nodes are market participants (colored by the app's real category
 * palette, labeled with real roster names when provided); energy pulses travel
 * the lines. Ambient pulses keep the scene breathing; `pulse()` fires bright
 * ones for real trades, so the hero literally moves with the market.
 *
 * Fully imperative (refs + one rAF loop, zero React state per frame). Pauses
 * when off-screen or the tab is hidden; renders a single static frame under
 * prefers-reduced-motion.
 */

export interface GridCanvasHandle {
  /** Fire n bright pulses (one per real trade). */
  pulse: (n: number) => void;
}

interface Props {
  /** Real participant roster (name + category) to project onto nodes. */
  roster?: { name: string; category: string }[];
  className?: string;
}

type Cat = "solar" | "wind" | "battery_load" | "llm" | "gas";

interface Node {
  x: number; // normalized 0..1
  y: number;
  r: number;
  cat: Cat;
  name: string;
  phase: number;
}

interface Edge {
  a: number;
  b: number;
}

interface Pulse {
  edge: number;
  t: number;
  speed: number;
  dir: 1 | -1;
  bright: boolean;
  color: string;
}

interface Ripple {
  x: number;
  y: number;
  t: number;
}

// Deterministic RNG so the constellation is identical every visit (a place, not noise).
function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const CAT_ORDER: Cat[] = ["solar", "wind", "battery_load", "llm", "gas"];
// Roughly the live roster's mix: many homes/batteries, solar+wind belts, few gas peakers.
const CAT_WEIGHTS: Record<Cat, number> = {
  solar: 10,
  wind: 8,
  battery_load: 15,
  llm: 6,
  gas: 3,
};

function catColor(cat: Cat): string {
  return CATEGORY_META[cat]?.color ?? "#94a3b8";
}

function buildScene(rng: () => number) {
  const nodes: Node[] = [];
  const total = CAT_ORDER.reduce((s, c) => s + CAT_WEIGHTS[c], 0);
  const minDist = 0.085;
  for (const cat of CAT_ORDER) {
    for (let i = 0; i < CAT_WEIGHTS[cat]; i++) {
      // Rejection-sample positions so nodes never clump unreadably.
      let x = 0.5;
      let y = 0.5;
      for (let attempt = 0; attempt < 40; attempt++) {
        x = 0.03 + rng() * 0.94;
        y = 0.06 + rng() * 0.88;
        if (nodes.every((n) => Math.hypot(n.x - x, n.y - y) > minDist)) break;
      }
      const big = cat === "llm" || cat === "gas";
      nodes.push({
        x,
        y,
        r: big ? 3.4 + rng() * 1.2 : 2.2 + rng() * 1.1,
        cat,
        name: "",
        phase: rng() * Math.PI * 2,
      });
    }
  }
  // Shuffle so categories interleave spatially in draw order.
  for (let i = nodes.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [nodes[i], nodes[j]] = [nodes[j], nodes[i]];
  }

  // Edges: each node to its 2 nearest neighbours + a few long transmission lines.
  const edges: Edge[] = [];
  const seen = new Set<string>();
  const addEdge = (a: number, b: number) => {
    const key = a < b ? `${a}-${b}` : `${b}-${a}`;
    if (a !== b && !seen.has(key)) {
      seen.add(key);
      edges.push({ a, b });
    }
  };
  nodes.forEach((n, i) => {
    const ranked = nodes
      .map((m, j) => ({ j, d: Math.hypot(m.x - n.x, m.y - n.y) }))
      .filter((e) => e.j !== i)
      .sort((p, q) => p.d - q.d);
    addEdge(i, ranked[0].j);
    addEdge(i, ranked[1].j);
  });
  for (let k = 0; k < 6; k++) {
    addEdge(Math.floor(rng() * total), Math.floor(rng() * total));
  }
  return { nodes, edges };
}

function hexToRgba(hex: string, alpha: number): string {
  const v = hex.replace("#", "");
  const r = parseInt(v.slice(0, 2), 16);
  const g = parseInt(v.slice(2, 4), 16);
  const b = parseInt(v.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

const GridCanvas = forwardRef<GridCanvasHandle, Props>(function GridCanvas(
  { roster, className },
  ref,
) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef(buildScene(mulberry32(20260702)));
  const pulsesRef = useRef<Pulse[]>([]);
  const ripplesRef = useRef<Ripple[]>([]);
  const pointerRef = useRef({ x: -1, y: -1, inside: false });
  const parallaxRef = useRef({ x: 0, y: 0 });
  const pendingPulsesRef = useRef(0);
  const runningRef = useRef(false);

  // Project the real roster onto nodes of the matching category.
  useEffect(() => {
    if (!roster?.length) return;
    const byCat = new Map<string, string[]>();
    for (const p of roster) {
      byCat.set(p.category, [...(byCat.get(p.category) ?? []), p.name]);
    }
    for (const node of sceneRef.current.nodes) {
      const pool = byCat.get(node.cat);
      if (pool?.length) node.name = pool.shift() ?? node.name;
    }
  }, [roster]);

  useImperativeHandle(ref, () => ({
    pulse: (n: number) => {
      pendingPulsesRef.current = Math.min(12, pendingPulsesRef.current + n);
    },
  }));

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const { nodes, edges } = sceneRef.current;
    const rng = mulberry32(7);
    let raf = 0;
    let lastAmbient = 0;
    let width = 0;
    let height = 0;

    const resize = () => {
      const rect = wrap.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const nodeXY = (n: Node): [number, number] => [
      n.x * width + parallaxRef.current.x * (0.5 + n.y),
      n.y * height + parallaxRef.current.y * (0.5 + n.y),
    ];

    const spawnPulse = (bright: boolean) => {
      const edge = Math.floor(rng() * edges.length);
      const color = catColor(nodes[edges[edge].a].cat);
      pulsesRef.current.push({
        edge,
        t: 0,
        speed: bright ? 0.9 + rng() * 0.5 : 0.35 + rng() * 0.3,
        dir: rng() > 0.5 ? 1 : -1,
        bright,
        color,
      });
      if (pulsesRef.current.length > 46) pulsesRef.current.shift();
    };

    const draw = (now: number) => {
      ctx.clearRect(0, 0, width, height);

      // Parallax eases toward the pointer (imperative; no React state per frame).
      const p = pointerRef.current;
      const targetX = p.inside ? (p.x / width - 0.5) * 16 : 0;
      const targetY = p.inside ? (p.y / height - 0.5) * 10 : 0;
      parallaxRef.current.x += (targetX - parallaxRef.current.x) * 0.04;
      parallaxRef.current.y += (targetY - parallaxRef.current.y) * 0.04;

      // Transmission lines.
      ctx.lineWidth = 1;
      for (const e of edges) {
        const [ax, ay] = nodeXY(nodes[e.a]);
        const [bx, by] = nodeXY(nodes[e.b]);
        ctx.strokeStyle = "rgba(148, 163, 184, 0.10)";
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.stroke();
      }

      // Energy pulses (a comet head + fading tail along its line).
      pulsesRef.current = pulsesRef.current.filter((pu) => pu.t <= 1);
      for (const pu of pulsesRef.current) {
        pu.t += pu.speed / 100;
        const e = edges[pu.edge];
        const from = pu.dir === 1 ? nodes[e.a] : nodes[e.b];
        const to = pu.dir === 1 ? nodes[e.b] : nodes[e.a];
        const [fx, fy] = nodeXY(from);
        const [tx, ty] = nodeXY(to);
        const hx = fx + (tx - fx) * pu.t;
        const hy = fy + (ty - fy) * pu.t;
        const tailT = Math.max(0, pu.t - (pu.bright ? 0.16 : 0.1));
        const tlx = fx + (tx - fx) * tailT;
        const tly = fy + (ty - fy) * tailT;
        const grad = ctx.createLinearGradient(tlx, tly, hx, hy);
        grad.addColorStop(0, hexToRgba(pu.color, 0));
        grad.addColorStop(1, hexToRgba(pu.color, pu.bright ? 0.9 : 0.45));
        ctx.strokeStyle = grad;
        ctx.lineWidth = pu.bright ? 2 : 1.2;
        ctx.beginPath();
        ctx.moveTo(tlx, tly);
        ctx.lineTo(hx, hy);
        ctx.stroke();
        ctx.fillStyle = hexToRgba(pu.color, pu.bright ? 1 : 0.6);
        ctx.beginPath();
        ctx.arc(hx, hy, pu.bright ? 2.4 : 1.6, 0, Math.PI * 2);
        ctx.fill();
      }

      // Nodes: soft aura + core; LLM agents slowly breathe.
      let hovered: { n: Node; x: number; y: number } | null = null;
      for (const n of nodes) {
        const [x, y] = nodeXY(n);
        const breathe =
          n.cat === "llm" ? 1 + 0.16 * Math.sin(now / 900 + n.phase) : 1;
        const r = n.r * breathe;
        const color = catColor(n.cat);
        ctx.fillStyle = hexToRgba(color, 0.14);
        ctx.beginPath();
        ctx.arc(x, y, r * 3.1, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = hexToRgba(color, 0.92);
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
        if (
          p.inside &&
          !hovered &&
          Math.hypot(p.x - x, p.y - y) < Math.max(26, r * 6)
        ) {
          hovered = { n, x, y };
        }
      }

      // Click ripples.
      ripplesRef.current = ripplesRef.current.filter((rp) => rp.t <= 1);
      for (const rp of ripplesRef.current) {
        rp.t += 0.02;
        ctx.strokeStyle = `rgba(34, 183, 232, ${0.5 * (1 - rp.t)})`;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(rp.x, rp.y, 8 + rp.t * 90, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Hover: halo ring + a small glass label with the real participant name.
      if (hovered) {
        const { n, x, y } = hovered;
        ctx.strokeStyle = hexToRgba(catColor(n.cat), 0.8);
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(x, y, n.r * 3.4, 0, Math.PI * 2);
        ctx.stroke();
        const meta = CATEGORY_META[n.cat];
        const label = n.name || meta?.label || n.cat;
        const sub = n.name ? (meta?.label ?? n.cat) : "participant";
        ctx.font = "600 12px Inter, system-ui, sans-serif";
        const w = Math.max(ctx.measureText(label).width, ctx.measureText(sub).width) + 22;
        const bx = Math.min(Math.max(8, x + 14), width - w - 8);
        const by = Math.min(Math.max(8, y - 44), height - 52);
        ctx.fillStyle = "rgba(10, 18, 32, 0.82)";
        ctx.strokeStyle = "rgba(255, 255, 255, 0.2)";
        ctx.beginPath();
        ctx.roundRect(bx, by, w, 40, 10);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = "#eef4ff";
        ctx.fillText(label, bx + 11, by + 17);
        ctx.font = "500 10px Inter, system-ui, sans-serif";
        ctx.fillStyle = hexToRgba(catColor(n.cat), 0.95);
        ctx.fillText(sub.toUpperCase(), bx + 11, by + 31);
      }
    };

    const frame = (now: number) => {
      if (!runningRef.current) return;
      if (now - lastAmbient > 750) {
        lastAmbient = now;
        if (rng() > 0.35) spawnPulse(false);
      }
      while (pendingPulsesRef.current > 0) {
        pendingPulsesRef.current -= 1;
        spawnPulse(true);
      }
      draw(now);
      raf = requestAnimationFrame(frame);
    };

    const start = () => {
      if (runningRef.current || reduced) return;
      runningRef.current = true;
      raf = requestAnimationFrame(frame);
    };
    const stop = () => {
      runningRef.current = false;
      cancelAnimationFrame(raf);
    };

    resize();
    if (reduced) {
      // One static, fully-lit frame: the constellation without the film.
      for (let i = 0; i < 6; i++) spawnPulse(false);
      pulsesRef.current.forEach((pu) => (pu.t = 0.5));
      draw(0);
    } else {
      start();
    }

    const ro = new ResizeObserver(() => {
      resize();
      if (reduced) draw(0);
    });
    ro.observe(wrap);

    const io = new IntersectionObserver(
      ([entry]) => {
        if (reduced) return;
        if (entry.isIntersecting) start();
        else stop();
      },
      { threshold: 0.05 },
    );
    io.observe(wrap);

    const onVisibility = () => {
      if (reduced) return;
      if (document.hidden) stop();
      else start();
    };
    document.addEventListener("visibilitychange", onVisibility);

    const onMove = (ev: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      pointerRef.current = {
        x: ev.clientX - rect.left,
        y: ev.clientY - rect.top,
        inside: true,
      };
    };
    const onLeave = () => {
      pointerRef.current.inside = false;
    };
    const onClick = (ev: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      ripplesRef.current.push({
        x: ev.clientX - rect.left,
        y: ev.clientY - rect.top,
        t: 0,
      });
      pendingPulsesRef.current = Math.min(12, pendingPulsesRef.current + 3);
    };
    if (!reduced) {
      canvas.addEventListener("pointermove", onMove);
      canvas.addEventListener("pointerleave", onLeave);
      canvas.addEventListener("pointerdown", onClick);
    }

    return () => {
      stop();
      ro.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("pointerdown", onClick);
    };
  }, []);

  return (
    <div ref={wrapRef} className={className} aria-hidden="true">
      <canvas ref={canvasRef} className="h-full w-full" />
    </div>
  );
});

export default memo(GridCanvas);
