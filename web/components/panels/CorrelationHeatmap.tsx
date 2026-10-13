"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { useData } from "@/components/AppData";
import { useLabels } from "@/lib/labels";

const CELL = 9;
const LABEL_W = 132;

/** Diverging fill: red (−1) through neutral grey (0) to blue (+1), mixed in OKLab. */
function fill(rho: number | null): string {
  if (rho === null) return "transparent"; // no area in common
  const pct = Math.round(Math.min(Math.abs(rho), 1) * 100);
  const pole = rho < 0 ? "var(--div-neg)" : "var(--div-pos)";
  return `color-mix(in oklab, ${pole} ${pct}%, var(--div-mid))`;
}

/** How much the scored indicators overlap (Spearman ρ), grouped by theme along the diagonal. */
export function CorrelationHeatmap() {
  const { manifest } = useData();
  const t = useTranslations("Heatmap");
  const labels = useLabels();
  const [hover, setHover] = useState<{ i: number; j: number } | null>(null);
  const c = manifest?.correlations;
  if (!manifest || !c || !c.ids.length) return null;
  const label = (id: string) => labels.indicator(id);
  const n = c.ids.length;
  const size = n * CELL;
  // Outline each theme's block on the diagonal
  const blocks: { start: number; end: number; theme: string }[] = [];
  c.themes.forEach((t, k) => {
    const last = blocks[blocks.length - 1];
    if (last && last.theme === t) last.end = k;
    else blocks.push({ start: k, end: k, theme: t });
  });
  const h = hover && {
    a: label(c.ids[hover.i]),
    b: label(c.ids[hover.j]),
    rho: c.rho[hover.i][hover.j],
  };

  return (
    <figure data-testid="correlations">
      <figcaption className="mb-1 text-xs font-medium text-[var(--text-secondary)]">
        {t("caption")}
      </figcaption>
      <svg
        width={LABEL_W + size}
        height={size}
        role="img"
        aria-label={t("aria")}
        onMouseLeave={() => setHover(null)}
      >
        {c.ids.map((id, i) => (
          <text
            key={id}
            x={LABEL_W - 4}
            y={i * CELL + CELL - 1.5}
            textAnchor="end"
            fontSize={8}
            fill={
              hover && (hover.i === i || hover.j === i)
                ? "var(--text-primary)"
                : "var(--text-muted)"
            }
          >
            {label(id).length > 26 ? `${label(id).slice(0, 25)}…` : label(id)}
          </text>
        ))}
        <g transform={`translate(${LABEL_W},0)`}>
          {c.rho.map((row, i) =>
            row.map((rho, j) => (
              <rect
                key={`${i}-${j}`}
                x={j * CELL}
                y={i * CELL}
                width={CELL - 1}
                height={CELL - 1}
                fill={fill(rho)}
                onMouseEnter={() => setHover({ i, j })}
              />
            )),
          )}
          {blocks.map((b) => (
            <rect
              key={b.theme}
              x={b.start * CELL - 0.5}
              y={b.start * CELL - 0.5}
              width={(b.end - b.start + 1) * CELL}
              height={(b.end - b.start + 1) * CELL}
              fill="none"
              stroke="var(--text-secondary)"
              strokeWidth={1}
            />
          ))}
        </g>
      </svg>
      <div className="mt-1 flex items-center gap-2 text-[10px] text-[var(--text-muted)]">
        <span>−1</span>
        <span
          className="h-2 w-28 rounded-sm"
          style={{
            background:
              "linear-gradient(to right, var(--div-neg), var(--div-mid), var(--div-pos))",
          }}
        />
        <span>+1</span>
        <span
          className="ml-auto min-h-[1em] text-[var(--text-secondary)]"
          aria-live="polite"
        >
          {h
            ? t("pair", {
                a: h.a,
                b: h.b,
                rho: h.rho === null ? "–" : h.rho.toFixed(2),
              })
            : t("legendHint")}
        </span>
      </div>
    </figure>
  );
}
