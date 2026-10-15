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
import type { AreaProfile, Comparison, Explanation, IndicatorInfo, Place, PoiResult, RankResult, WeightProfile } from "@/lib/contracts.gen";

const anyArgs = z.record(z.string(), z.unknown());

const TOOLS = [
  "rank_areas", "get_area_profile", "compare_areas", "nearest_pois", "explain_score", "set_weights",
  "show_on_map", "run_sql", "search_place", "list_indicators", "add_to_shortlist", "remove_from_shortlist",
  "set_profile",
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
function NoResult({ name }: { name: string }) {
  const t = useTranslations("Registry");
  return <Chip>{t("noResult", { name })}</Chip>;
}

function useToolCard<T>(name: string, render: (result: T, args: Record<string, unknown>) => React.ReactElement | null) {
  useRenderTool(
    {
      name,
      parameters: anyArgs,
      render: ({ status, result, parameters }) => {
        if (status !== "complete") return <Pending name={name} />;
        const data = parseResult<T>(result);
        if (data == null) return <NoResult name={name} />;
        return render(data, (parameters ?? {}) as Record<string, unknown>);
      },
    },
    [],
  );
}

function WeightsChip({ w }: { w: { preset: string; theme_weights: Record<string, number> } }) {
  const t = useTranslations("Registry");
  const labels = useLabels();
  return (
    <Chip>
      {t("weightsSet")} <strong className="font-medium text-[var(--text-primary)]">{labels.preset(w.preset)}</strong>
      {Object.entries(w.theme_weights)
        .filter(([, v]) => v !== 1)
        .map(([theme, v]) => (
          <span key={theme}>· {labels.theme(theme)} ×{v}</span>
        ))}
    </Chip>
  );
}

function ProfileChip({ profile }: { profile: WeightProfile }) {
  const t = useTranslations("Registry");
  return (
    <Chip>
      {t("profileSet")} <strong className="font-medium text-[var(--text-primary)]">{profile.name}</strong>
      <span>· {profile.rationale}</span>
    </Chip>
  );
}

function MapChip({ layer }: { layer: string }) {
  const t = useTranslations("Registry");
  const labels = useLabels();
  const [kind, id] = layer.includes(":") ? layer.split(":", 2) : ["overall", layer];
  const name = kind === "theme" ? labels.theme(id) : kind === "indicator" ? labels.indicator(id) : layer;
  return <Chip>{t("mapUpdated", { layer: name })}</Chip>;
}

function ShortlistChip({ count }: { count: number }) {
  const t = useTranslations("Registry");
  return <Chip>{t("shortlistSaved", { count })}</Chip>;
}

function FoundChip({ places }: { places: Place[] }) {
  const t = useTranslations("Registry");
  return (
    <Chip>
      {t("found", {
        places: places.slice(0, 3).map((p) => `${p.name}${p.detail ? ` (${p.detail})` : ""}`).join("; ") || t("nothing"),
      })}
    </Chip>
  );
}

function IndicatorsChip({ items }: { items: Pick<IndicatorInfo, "id" | "role">[] }) {
  const t = useTranslations("Registry");
  const labels = useLabels();
  return (
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
  );
}

/** Each tool's card. The renderers are registered once, so every chip is a component
 * of its own: it reads the language and the catalogue through hooks when it renders. */
export function GenerativeUI() {
  useToolCard<RankResult>("rank_areas", (r) => <RankedList result={r} />);
  useToolCard<AreaProfile>("get_area_profile", (p) => <AreaProfileCard profile={p} />);
  useToolCard<Comparison>("compare_areas", (c) => <ComparisonTable comparison={c} />);
  useToolCard<PoiResult>("nearest_pois", (r) => <PoiList result={r} />);
  useToolCard<Explanation>("explain_score", (e) => <MethodExplainer explanation={e} />);
  useToolCard<SqlData>("run_sql", (d, args) => <SqlResult data={d} query={String(args.query ?? "")} />);
  useToolCard<{ preset: string; theme_weights: Record<string, number> }>("set_weights", (w) => <WeightsChip w={w} />);
  useToolCard<WeightProfile>("set_profile", (p) => <ProfileChip profile={p} />);
  useToolCard<{ layer: string }>("show_on_map", (m) => <MapChip layer={m.layer} />);
  useToolCard<{ shortlist: unknown[] }>("add_to_shortlist", (r) => <ShortlistChip count={r.shortlist.length} />);
  useToolCard<{ shortlist: unknown[] }>("remove_from_shortlist", (r) => <ShortlistChip count={r.shortlist.length} />);
  useToolCard<Place[]>("search_place", (places) => <FoundChip places={places} />);
  useToolCard<Pick<IndicatorInfo, "id" | "role">[]>("list_indicators", (items) => <IndicatorsChip items={items} />);
  return null;
}
