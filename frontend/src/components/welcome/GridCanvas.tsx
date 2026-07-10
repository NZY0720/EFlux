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
 * sagging power lines with additive glow. Ambient pulses keep the scene
 * breathing; `pulse()` fires bright ones for real trades, each landing with an
 * impact ring. The cursor carries a soft electric field that lights up nearby
 * lines, and clicking discharges into the grid.
 *
 * Fully imperative (refs + one rAF loop, zero React state per frame). Pauses
 * when off-screen or the tab is hidden; renders a single static frame under
 * prefers-reduced-motion.
 */

export interface GridCanvasHandle {
  /** Fire n bright pulses (one per real trade). */
  pulse: (n: number) => void;
  /** Show a brief speech bubble on the named agent's node; false if the name has no node. */
  speak: (name: string, text: string) => boolean;
  /** Drive solar nodes with the simulation's actual sun (hour 0-24, null = unknown). */
  setSimHour: (hour: number | null) => void;
}

interface Props {
  /** Real participant roster (name + category) to project onto nodes. */
  roster?: { name: string; category: string }[];
  className?: string;
  /** Scales the ambient constellation without removing the live-grid interaction. */
  density?: number;
  /** Probability of omitting an ambient node from the left-side hero copy zone. */
  copyClearance?: number;
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
  sag: number; // catenary-ish droop, px at reference width
  long: boolean;
}

interface Pulse {
  a: number;
  b: number;
  sag: number;
  t: number;
  speed: number;
  bright: boolean;
  color: string;
}

interface Ripple {
  x: number;
  y: number;
  t: number;
  color: string;
  max: number;
}

interface Bubble {
  node: number;
  text: string;
  until: number; // performance.now() deadline
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

function buildScene(rng: () => number, density = 1, copyClearance = 0) {
  const nodes: Node[] = [];
  const minDist = 0.085;
  for (const cat of CAT_ORDER) {
    const count = Math.max(1, Math.round(CAT_WEIGHTS[cat] * density));
    for (let i = 0; i < count; i++) {
      // Rejection-sample positions so nodes never clump unreadably.
      let x = 0.5;
      let y = 0.5;
      for (let attempt = 0; attempt < 40; attempt++) {
        x = 0.03 + rng() * 0.94;
        y = 0.06 + rng() * 0.88;
        const inCopyZone = x < 0.5 && y > 0.16 && y < 0.84;
        if ((!inCopyZone || rng() > copyClearance) && nodes.every((n) => Math.hypot(n.x - x, n.y - y) > minDist)) break;
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

  // Edges: each node to its 2 nearest neighbours + a few long transmission arcs.
  const edges: Edge[] = [];
  const seen = new Set<string>();
  const addEdge = (a: number, b: number, long = false) => {
    const key = a < b ? `${a}-${b}` : `${b}-${a}`;
    if (a !== b && !seen.has(key)) {
      seen.add(key);
      const d = Math.hypot(nodes[a].x - nodes[b].x, nodes[a].y - nodes[b].y);
      // Longer spans droop more, like real conductors.
      edges.push({ a, b, sag: (6 + d * 60) * (0.7 + rng() * 0.6), long });
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
  for (let k = 0; k < Math.max(3, Math.round(7 * density)); k++) {
    addEdge(Math.floor(rng() * nodes.length), Math.floor(rng() * nodes.length), true);
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

/** Point on the sagging line (quadratic Bezier with a dropped control point). */
function curvePoint(
  ax: number,
  ay: number,
  bx: number,
  by: number,
  sag: number,
  t: number,
): [number, number] {
  const cx = (ax + bx) / 2;
  const cy = (ay + by) / 2 + sag;
  const u = 1 - t;
  return [
    u * u * ax + 2 * u * t * cx + t * t * bx,
    u * u * ay + 2 * u * t * cy + t * t * by,
  ];
}

const GridCanvas = forwardRef<GridCanvasHandle, Props>(function GridCanvas(
  { roster, className, density = 1, copyClearance = 0 },
  ref,
) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef(buildScene(mulberry32(20260702), density, copyClearance));
  const pulsesRef = useRef<Pulse[]>([]);
  const ripplesRef = useRef<Ripple[]>([]);
  const bubblesRef = useRef<Bubble[]>([]);
  const simHourRef = useRef<number | null>(null);
  const pointerRef = useRef({ x: -1, y: -1, inside: false });
  const parallaxRef = useRef({ x: 0, y: 0 });
  const pendingPulsesRef = useRef(0);
  const runningRef = useRef(false);

  // Project the real roster onto nodes of the matching category (idempotent across
  // re-runs: named nodes keep their names, pools skip already-taken ones).
  useEffect(() => {
    if (!roster?.length) return;
    const { nodes, edges } = sceneRef.current;
    const byCat = new Map<string, string[]>();
    for (const p of roster) {
      byCat.set(p.category, [...(byCat.get(p.category) ?? []), p.name]);
    }
    const taken = new Set(nodes.map((n) => n.name).filter(Boolean));
    for (const node of nodes) {
      const pool = byCat.get(node.cat);
      while (pool?.length && taken.has(pool[0])) pool.shift();
      if (!node.name && pool?.length) {
        node.name = pool.shift() as string;
        taken.add(node.name);
      }
    }
    // LLM agents that didn't fit a pre-built slot get their OWN node — user-created
    // managed agents land here, and they belong on the grid more than anyone.
    // Position is deterministic per name, wired to its two nearest neighbours.
    const leftover = (byCat.get("llm") ?? []).filter((n) => n && !taken.has(n)).slice(0, 6);
    for (const name of leftover) {
      let hash = 0;
      for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
      const rng = mulberry32(hash || 1);
      let x = 0.5;
      let y = 0.5;
      for (let attempt = 0; attempt < 40; attempt++) {
        x = 0.06 + rng() * 0.88;
        y = 0.08 + rng() * 0.84;
        if (nodes.every((n) => Math.hypot(n.x - x, n.y - y) > 0.07)) break;
      }
      const idx = nodes.length;
      nodes.push({ x, y, r: 3.6, cat: "llm", name, phase: rng() * Math.PI * 2 });
      taken.add(name);
      const ranked = nodes
        .slice(0, idx)
        .map((m, j) => ({ j, d: Math.hypot(m.x - x, m.y - y) }))
        .sort((p, q) => p.d - q.d);
      for (const nb of ranked.slice(0, 2)) {
        edges.push({ a: idx, b: nb.j, sag: 6 + nb.d * 60, long: false });
      }
    }
  }, [roster]);

  useImperativeHandle(ref, () => ({
    pulse: (n: number) => {
      pendingPulsesRef.current = Math.min(12, pendingPulsesRef.current + n);
    },
    speak: (name: string, text: string) => {
      const idx = sceneRef.current.nodes.findIndex((n) => n.name === name);
      if (idx === -1 || !text) return false;
      const line = text.length > 44 ? `${text.slice(0, 43)}…` : text;
      // One bubble per node; at most 3 on screen so the grid stays a grid.
      bubblesRef.current = bubblesRef.current.filter((b) => b.node !== idx).slice(-2);
      bubblesRef.current.push({ node: idx, text: line, until: performance.now() + 5200 });
      return true;
    },
    setSimHour: (hour: number | null) => {
      simHourRef.current = hour;
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
      const e = edges[Math.floor(rng() * edges.length)];
      const flip = rng() > 0.5;
      pulsesRef.current.push({
        a: flip ? e.b : e.a,
        b: flip ? e.a : e.b,
        sag: e.sag,
        t: 0,
        speed: bright ? 0.85 + rng() * 0.5 : 0.32 + rng() * 0.28,
        bright,
        color: catColor(nodes[flip ? e.b : e.a].cat),
      });
      if (pulsesRef.current.length > 48) pulsesRef.current.shift();
    };

    const draw = (now: number) => {
      ctx.clearRect(0, 0, width, height);

      // Parallax eases toward the pointer (imperative; no React state per frame).
      const p = pointerRef.current;
      const targetX = p.inside ? (p.x / width - 0.5) * 16 : 0;
      const targetY = p.inside ? (p.y / height - 0.5) * 10 : 0;
      parallaxRef.current.x += (targetX - parallaxRef.current.x) * 0.04;
      parallaxRef.current.y += (targetY - parallaxRef.current.y) * 0.04;

      // Power lines: sagging curves; those near the cursor pick up charge.
      const FIELD_R = 170;
      for (const e of edges) {
        const [ax, ay] = nodeXY(nodes[e.a]);
        const [bx, by] = nodeXY(nodes[e.b]);
        const [mx, my] = curvePoint(ax, ay, bx, by, e.sag, 0.5);
        let alpha = e.long ? 0.08 : 0.15;
        let color = `rgba(148, 163, 184, ${alpha})`;
        if (p.inside) {
          const d = Math.hypot(p.x - mx, p.y - my);
          if (d < FIELD_R) {
            const boost = 1 - d / FIELD_R;
            alpha = alpha + boost * 0.32;
            color = `rgba(93, 205, 240, ${alpha})`;
          }
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.quadraticCurveTo((ax + bx) / 2, (ay + by) / 2 + e.sag, bx, by);
        ctx.stroke();
      }

      // Energy pulses: additive glow, comet head + fading tail along the curve.
      ctx.globalCompositeOperation = "lighter";
      const finished: Pulse[] = [];
      pulsesRef.current = pulsesRef.current.filter((pu) => {
        if (pu.t > 1) {
          finished.push(pu);
          return false;
        }
        return true;
      });
      for (const pu of finished) {
        if (!pu.bright) continue;
        // A real trade lands: impact ring at the destination node.
        const [x, y] = nodeXY(nodes[pu.b]);
        ripplesRef.current.push({ x, y, t: 0, color: pu.color, max: 34 });
      }
      for (const pu of pulsesRef.current) {
        pu.t += pu.speed / 100;
        const [ax, ay] = nodeXY(nodes[pu.a]);
        const [bx, by] = nodeXY(nodes[pu.b]);
        const t = Math.min(1, pu.t);
        const [hx, hy] = curvePoint(ax, ay, bx, by, pu.sag, t);
        const steps = 5;
        const tail = pu.bright ? 0.17 : 0.11;
        for (let s = 0; s < steps; s++) {
          const t0 = Math.max(0, t - tail * ((s + 1) / steps));
          const t1 = Math.max(0, t - tail * (s / steps));
          const [x0, y0] = curvePoint(ax, ay, bx, by, pu.sag, t0);
          const [x1, y1] = curvePoint(ax, ay, bx, by, pu.sag, t1);
          const a = (pu.bright ? 0.75 : 0.4) * (1 - s / steps);
          ctx.strokeStyle = hexToRgba(pu.color, a);
          ctx.lineWidth = pu.bright ? 2.1 : 1.3;
          ctx.beginPath();
          ctx.moveTo(x0, y0);
          ctx.lineTo(x1, y1);
          ctx.stroke();
        }
        // Head: hot core + soft halo.
        ctx.fillStyle = hexToRgba(pu.color, pu.bright ? 0.5 : 0.25);
        ctx.beginPath();
        ctx.arc(hx, hy, pu.bright ? 5 : 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
        ctx.beginPath();
        ctx.arc(hx, hy, pu.bright ? 1.7 : 1.1, 0, Math.PI * 2);
        ctx.fill();
      }

      // Nodes: twinkle + soft aura; LLM agents breathe inside a slow orbit ring
      // (the minds, marked apart from the machines); solar follows the sim's sun.
      const hour = simHourRef.current;
      const daylight =
        hour === null ? 1 : Math.max(0, Math.sin(((hour - 6) / 12) * Math.PI));
      let hovered: { n: Node; x: number; y: number } | null = null;
      for (const n of nodes) {
        const [x, y] = nodeXY(n);
        const breathe =
          n.cat === "llm" ? 1 + 0.16 * Math.sin(now / 900 + n.phase) : 1;
        const twinkle = 0.82 + 0.18 * Math.sin(now / 1400 + n.phase * 2.3);
        // Solar plants sleep at night, exactly when the simulation says so.
        const sun = n.cat === "solar" ? 0.25 + 0.75 * daylight : 1;
        const r = n.r * breathe;
        const color = catColor(n.cat);
        let auraBoost = 0;
        if (p.inside) {
          const d = Math.hypot(p.x - x, p.y - y);
          if (d < FIELD_R) auraBoost = (1 - d / FIELD_R) * 0.2;
          if (!hovered && d < Math.max(26, r * 6)) hovered = { n, x, y };
        }
        ctx.fillStyle = hexToRgba(color, (0.1 + auraBoost) * sun);
        ctx.beginPath();
        ctx.arc(x, y, r * 3.2, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = hexToRgba(color, 0.9 * twinkle * sun);
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
        if (n.cat === "llm") {
          ctx.strokeStyle = hexToRgba(color, 0.28);
          ctx.lineWidth = 1;
          ctx.setLineDash([4, 6]);
          ctx.lineDashOffset = -(now / 40 + n.phase * 12);
          ctx.beginPath();
          ctx.arc(x, y, r * 4.4, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }

      // Impact + click ripples.
      ripplesRef.current = ripplesRef.current.filter((rp) => rp.t <= 1);
      for (const rp of ripplesRef.current) {
        rp.t += rp.max > 40 ? 0.02 : 0.035;
        ctx.strokeStyle = hexToRgba(rp.color, 0.55 * (1 - rp.t));
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(rp.x, rp.y, 4 + rp.t * rp.max, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalCompositeOperation = "source-over";

      // Chatroom speech: when an agent posts, its node says the line on the grid.
      bubblesRef.current = bubblesRef.current.filter((b) => b.until > now);
      for (const b of bubblesRef.current) {
        const n = nodes[b.node];
        const [x, y] = nodeXY(n);
        const fade = Math.min(1, (b.until - now) / 500); // ease out at the end
        ctx.font = "500 11px Inter, system-ui, sans-serif";
        const w = ctx.measureText(b.text).width + 20;
        const bx = Math.min(Math.max(8, x - w / 2), width - w - 8);
        const by = Math.max(8, y - n.r * 4.4 - 34);
        ctx.globalAlpha = fade;
        ctx.fillStyle = "rgba(10, 18, 32, 0.88)";
        ctx.strokeStyle = hexToRgba(catColor(n.cat), 0.55);
        ctx.beginPath();
        ctx.roundRect(bx, by, w, 24, 12);
        ctx.fill();
        ctx.stroke();
        // Pointer toward the speaking node.
        ctx.fillStyle = "rgba(10, 18, 32, 0.88)";
        ctx.beginPath();
        ctx.moveTo(x - 4, by + 24);
        ctx.lineTo(x + 4, by + 24);
        ctx.lineTo(x, by + 30);
        ctx.closePath();
        ctx.fill();
        ctx.fillStyle = "#dbe7f7";
        ctx.fillText(b.text, bx + 10, by + 16);
        ctx.globalAlpha = 1;
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
      if (now - lastAmbient > 700) {
        lastAmbient = now;
        if (rng() > 0.32) spawnPulse(false);
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
        color: "#22b7e8",
        max: 90,
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
