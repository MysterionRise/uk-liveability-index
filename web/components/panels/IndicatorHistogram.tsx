"use client";

import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { themeVar } from "@/lib/palette";

/** Distribution of a 0–100 score across every scored LSOA, with the selected area marked. Bars take the theme's colour (secondary ink for the overall score, so the marker stays legible). */
export function IndicatorHistogram({
  values,
  label,
  marker,
  theme,
}: {
  values: Float64Array;
  label: string;
  marker?: number | null;
  /** Theme of the layer shown, for the bar colour (ink for the overall score) */
  theme?: string | null;
}) {
  const BINS = 20;
  const t = useTranslations("Histogram");
  const [hover, setHover] = useState<number | null>(null);
  const counts = useMemo(() => {
    const c = new Array(BINS).fill(0);
    for (const v of values) if (!Number.isNaN(v)) c[Math.min(BINS - 1, Math.floor(v / (100 / BINS)))]++;
    return c;
  }, [values]);
  const max = Math.max(...counts, 1);
  const W = 320, H = 120, PAD = 18;
  const bw = (W - PAD) / BINS;

  return (
    <figure className="text-xs" data-testid="histogram">
      <figcaption className="mb-1 font-medium">{t("caption", { label })}</figcaption>
      <svg viewBox={`0 0 ${W} ${H + 18}`} className="w-full" role="img" aria-label={t("aria", { label })}>
        <line x1={PAD} x2={W} y1={H} y2={H} stroke="var(--baseline)" />
        {counts.map((c, i) => {
          const h = (c / max) * (H - 8);
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              {/* Hit target taller than the bar */}
              <rect x={PAD + i * bw} y={0} width={bw} height={H} fill="transparent" />
              <rect
                x={PAD + i * bw + 1}
                y={H - h}
                width={bw - 2}
                height={h}
                rx={2}
                fill={theme ? themeVar(theme) : "var(--text-secondary)"}
                opacity={hover === null || hover === i ? 1 : 0.55}
              />
            </g>
          );
        })}
        {marker != null && !Number.isNaN(marker) && (
          <g>
            <line x1={PAD + (marker / 100) * (W - PAD)} x2={PAD + (marker / 100) * (W - PAD)} y1={4} y2={H} stroke="var(--text-primary)" strokeWidth={2} />
            {/* The label sits to the left of the line near the right edge, so it never overflows */}
            <text
              x={PAD + (marker / 100) * (W - PAD) + (marker > 80 ? -3 : 3)}
              y={12}
              textAnchor={marker > 80 ? "end" : "start"}
              fill="var(--text-primary)"
              stroke="var(--surface-1)"
              strokeWidth={3}
              paintOrder="stroke"
              fontSize={10}
            >
              {t("selected", { value: Math.round(marker) })}
            </text>
          </g>
        )}
        {[0, 50, 100].map((t) => (
          <text key={t} x={PAD + (t / 100) * (W - PAD)} y={H + 13} textAnchor={t === 0 ? "start" : t === 100 ? "end" : "middle"} fill="var(--text-muted)" fontSize={10}>{t}</text>
        ))}
      </svg>
      <div className="h-4 text-[var(--text-secondary)]">
        {hover !== null && t("hover", { lo: hover * 5, hi: hover * 5 + 5, count: counts[hover] })}
      </div>
    </figure>
  );
}
