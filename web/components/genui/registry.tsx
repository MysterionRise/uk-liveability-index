"use client";

/**
 * Generative UI: one renderer per assistant tool. The assistant's tool results are
 * typed (contracts.gen.ts), so each call becomes a component in the chat. This file is
 * the only place that knows about CopilotKit's renderer API.
 */
import { useRenderTool } from "@copilotkit/react-core/v2";
import { useTranslations } from "next-intl";
import { z } from "zod";

import { AreaProfileCard } from "@/components/genui/AreaProfileCard";
import { ComparisonTable } from "@/components/genui/ComparisonTable";
import { MethodExplainer } from "@/components/genui/MethodExplainer";
import { PoiList } from "@/components/genui/PoiList";
import { RankedList } from "@/components/genui/RankedList";
import { type SqlData, SqlResult } from "@/components/genui/SqlResult";
import { parseResult } from "@/components/ui";
import { useLabels } from "@/lib/labels";
import type { AreaProfile, Comparison, Explanation, IndicatorInfo, Place, PoiResult, RankResult } from "@/lib/contracts.gen";

const anyArgs = z.record(z.string(), z.unknown());

const TOOLS = [
  "rank_areas", "get_area_profile", "compare_areas", "nearest_pois", "explain_score", "set_weights",
  "show_on_map", "run_sql", "search_place", "list_indicators", "add_to_shortlist", "remove_from_shortlist",
] as const;

function Pending({ name }: { name: string }) {
  const t = useTranslations("Registry");
  const known = (TOOLS as readonly string[]).includes(name);
  return (
    <div className="my-1 inline-flex items-center gap-2 rounded-full border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--text-secondary)]">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
      {known ? t(`working.${name as (typeof TOOLS)[number]}`) : t("running", { name })}
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <div className="my-1 inline-flex flex-wrap items-center gap-1 rounded-full border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--text-secondary)]">
      {children}
    </div>
  );
}

/** Register one renderer: pending chip while running, the component once there's a result. */
function useToolCard<T>(
  name: string,
  render: (result: T, args: Record<string, unknown>) => React.ReactElement | null,
  deps: unknown[] = [],
) {
  const t = useTranslations("Registry");
  useRenderTool(
    {
      name,
      parameters: anyArgs,
      render: ({ status, result, parameters }) => {
        if (status !== "complete") return <Pending name={name} />;
        const data = parseResult<T>(result);
        if (data == null) return <Chip>{t("noResult", { name })}</Chip>;
        return render(data, (parameters ?? {}) as Record<string, unknown>);
      },
    },
    // The chips read the language and the catalogue: re-register when either changes,
    // or the first render's English and raw ids stick
    [t, ...deps],
  );
}

export function GenerativeUI() {
  const t = useTranslations("Registry");
  const labels = useLabels();
  // Registrations capture t and labels: a change in either re-registers every card
  const deps = [t, labels];
  useToolCard<RankResult>("rank_areas", (r) => <RankedList result={r} />, deps);
  useToolCard<AreaProfile>("get_area_profile", (p) => <AreaProfileCard profile={p} />, deps);
  useToolCard<Comparison>("compare_areas", (c) => <ComparisonTable comparison={c} />, deps);
  useToolCard<PoiResult>("nearest_pois", (r) => <PoiList result={r} />, deps);
  useToolCard<Explanation>("explain_score", (e) => <MethodExplainer explanation={e} />, deps);
  useToolCard<SqlData>("run_sql", (d, args) => <SqlResult data={d} query={String(args.query ?? "")} />, deps);
  useToolCard<{ preset: string; theme_weights: Record<string, number> }>("set_weights", (w) => (
    <Chip>
      {t("weightsSet")} <strong className="font-medium text-[var(--text-primary)]">{w.preset}</strong>
      {Object.entries(w.theme_weights)
        .filter(([, v]) => v !== 1)
        .map(([t, v]) => (
          <span key={t}>· {t} ×{v}</span>
        ))}
    </Chip>
  ), deps);
  useToolCard<{ layer: string }>("show_on_map", (m) => <Chip>{t("mapUpdated", { layer: m.layer })}</Chip>, deps);
  useToolCard<{ shortlist: unknown[] }>("add_to_shortlist", (r) => <Chip>{t("shortlistSaved", { count: r.shortlist.length })}</Chip>, deps);
  useToolCard<{ shortlist: unknown[] }>("remove_from_shortlist", (r) => <Chip>{t("shortlistSaved", { count: r.shortlist.length })}</Chip>, deps);
  useToolCard<Place[]>("search_place", (places) => (
    <Chip>
      {t("found", {
        places: places.slice(0, 3).map((p) => `${p.name}${p.detail ? ` (${p.detail})` : ""}`).join("; ") || t("nothing"),
      })}
    </Chip>
  ), deps);
  useToolCard<Pick<IndicatorInfo, "id" | "role">[]>("list_indicators", (items) => (
    <details className="my-1 text-xs">
      <summary className="cursor-pointer text-[var(--text-secondary)]">{t("indicatorsCount", { count: items.length })}</summary>
      <ul className="mt-1 space-y-1">
        {items.map((i) => (
          <li key={i.id}>
            <span className="font-medium">{labels.indicator(i.id)}</span>{" "}
            <span className="text-[var(--text-muted)]">({i.role}, {labels.unit(i.id)})</span>
            <div className="text-[var(--text-secondary)]">{labels.indicatorDescription(i.id)}</div>
          </li>
        ))}
      </ul>
    </details>
  ), deps);
  return null;
}
