"use client";

import { useLocale, useTranslations } from "next-intl";
import { Fragment, useState } from "react";

import { useData, useLiveability } from "@/components/AppData";
import { Band, Card, Muted, ScoreBar, ValueWithUnit, formatValue, useQualityNote } from "@/components/ui";
import type { AreaProfile, IndicatorValue } from "@/lib/contracts.gen";
import { useLabels, usePresetLabel } from "@/lib/labels";
import { bboxOf } from "@/lib/state";

function Facts({ items }: { items: IndicatorValue[] }) {
  const locale = useLocale();
  const qualityNote = useQualityNote();
  const labels = useLabels();
  const tq = useTranslations("Quality");
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
      {items.map((f) => (
        <div key={f.id} className="min-w-0">
          <dt
            className="truncate text-xs text-[var(--text-muted)]"
            title={
              f.stands_in_for
                ? `${labels.indicator(f.id)} (${tq("standsInFor", { label: labels.indicator(f.stands_in_for) })})`
                : labels.indicator(f.id)
            }
          >
            {labels.indicator(f.id)}
            {f.stands_in_for && (
              <span className="ml-1 text-[10px]">
                · {tq("standsInFor", { label: labels.indicator(f.stands_in_for) })}
              </span>
            )}
          </dt>
          <dd className="text-sm tabular-nums">
            <ValueWithUnit value={f.value} id={f.id} unit={f.unit} />
            {qualityNote(f.quality) && (
              <span className="ml-1 text-xs text-[var(--text-muted)]">
                ({qualityNote(f.quality)})
              </span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Copies a link that opens this area with the current weights (the URL hash holds the view). */
function CopyLink({ code }: { code: string }) {
  const t = useTranslations("Profile");
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="btn"
      onClick={async () => {
        const url = new URL(window.location.href);
        try {
          const view = JSON.parse(
            decodeURIComponent(url.hash.slice(1) || "{}"),
          );
          url.hash = encodeURIComponent(JSON.stringify({ ...view, s: code }));
        } catch {
          /* keep the current hash */
        }
        try {
          await navigator.clipboard.writeText(url.toString());
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        } catch {
          window.prompt(t("copyPrompt"), url.toString());
        }
      }}
    >
      {copied ? t("linkCopied") : t("copyLink")}
    </button>
  );
}

export function AreaProfileCard({ profile }: { profile: AreaProfile }) {
  const { state, update } = useLiveability();
  const { manifest } = useData();
  const t = useTranslations("Profile");
  const tw = useTranslations("Weights");
  const tn = useTranslations("Nations");
  // The card's benchmark follows "compare against": within the nation when that is
  // chosen (the urban/rural comparison has no per-area percentile, so it reads as the UK)
  const withinNation = state.compare_within === "nation";
  const nationName = tn(profile.nation as "E" | "W" | "S" | "N");
  const nationOf = tn(`of_${profile.nation}` as "of_E" | "of_W" | "of_S" | "of_N");
  // Themes this nation declares it cannot score (shown as limited data)
  const gaps = new Set(manifest?.geography?.nations?.[profile.nation]?.theme_gaps ?? []);
  const labels = useLabels();
  const presetName = usePresetLabel();
  const saved = state.shortlist.some((i) => i.code === profile.lsoa21cd);
  // Without a separate neighbourhood (a nation whose small areas are their own mid
  // tier) the area itself heads the card beside its council
  const title = `${profile.neighbourhood ?? profile.lsoa_name}, ${profile.local_authority}`;
  // The English name beside a Welsh or Gaelic one, so a place stays recognisable
  const english = [profile.neighbourhood_en, profile.local_authority_en]
    .filter(Boolean)
    .join(", ");

  return (
    <Card
      testId="area-profile"
      title={title}
      subtitle={`${english ? `${english} · ` : ""}${
        profile.neighbourhood
          ? t("subtitle", { area: profile.lsoa_name, ruc: profile.urban_rural, population: profile.population })
          : t("subtitleOwnTier", { ruc: profile.urban_rural, population: profile.population })
      }`}
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-3xl font-semibold tabular-nums">
          {profile.overall?.toFixed(0) ?? "–"}
        </span>
        <span className="text-xs text-[var(--text-muted)]">
          {t("overallOf100", { preset: presetName(profile.preset) })}
        </span>
      </div>
      <div className="mt-1">
        <Band
          band={profile.band}
          percentile={withinNation ? profile.overall_percentile_nation : profile.overall_percentile}
          nationOf={withinNation ? nationOf : undefined}
        />
      </div>
      {!withinNation &&
        profile.overall_percentile_range &&
        profile.overall_percentile_range[0] !== profile.overall_percentile_range[1] && (
          <p
            className="mt-1 text-xs text-[var(--text-muted)]"
            data-testid="percentile-range"
            title={t("rangeTitle")}
          >
            {t("range", {
              lo: profile.overall_percentile_range[0],
              hi: profile.overall_percentile_range[1],
            })}
          </p>
        )}

      <div className="mt-3" data-testid="theme-bars">
        {profile.themes.map((th) => (
          <Fragment key={th.theme}>
          <ScoreBar
            label={labels.theme(th.theme)}
            score={th.score}
            theme={th.theme}
            hint={
              withinNation
                ? th.percentile_nation != null
                  ? t("betterThanNation", { pct: th.percentile_nation, nationOf })
                  : undefined
                : th.percentile != null
                  ? t("betterThan", { pct: th.percentile })
                  : undefined
            }
            marks={[
              {
                value: th.local_median,
                kind: "local",
                label: t("localMedian", { area: profile.local_authority }),
              },
              {
                value: withinNation ? th.nation_median : th.country_median,
                kind: "england",
                label: withinNation ? t("nationMedian", { nation: nationName }) : t("ukMedian"),
              },
            ]}
          />
          {th.score == null && gaps.has(th.theme) && (
            <p className="mb-1 text-[10px] text-[var(--text-muted)]">{t("limitedData")}</p>
          )}
          </Fragment>
        ))}
        <p className="mt-1 flex gap-3 text-[10px] text-[var(--text-muted)]">
          <span className="inline-flex items-center gap-1">
            <span
              className="inline-block h-2.5 w-[2px] bg-[var(--text-primary)]"
              aria-hidden
            />{" "}
            {t("localMedian", { area: profile.local_authority })}
          </span>
          <span className="inline-flex items-center gap-1">
            <span
              className="inline-block h-2.5 w-px bg-[var(--text-muted)]"
              aria-hidden
            />{" "}
            {withinNation ? t("nationMedian", { nation: nationName }) : t("ukMedian")}
          </span>
        </p>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="mb-1 font-medium text-[var(--text-secondary)]">
            {t("strengths")}
          </div>
          {profile.strengths.map((s) => (
            <div key={s.id}>{labels.indicator(s.id)}</div>
          ))}
        </div>
        <div>
          <div className="mb-1 font-medium text-[var(--text-secondary)]">
            {t("weakerSpots")}
          </div>
          {profile.weaknesses.map((s) => (
            <div key={s.id}>{labels.indicator(s.id)}</div>
          ))}
        </div>
      </div>

      <div className="mt-3 border-t border-[var(--border)] pt-2">
        <Facts items={profile.key_facts} />
      </div>

      {profile.flags && profile.flags.length > 0 && (
        <ul className="mt-2 list-disc pl-4 text-xs text-[var(--text-secondary)]">
          {profile.flags.map((f, i) => (
            <li key={f}>{labels.flag(profile.flag_codes?.[i] ?? f, f)}</li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex gap-2">
        <button
          className="btn"
          onClick={() =>
            update((s) => ({
              ...s,
              map: {
                ...s.map,
                selected: profile.lsoa21cd,
                bbox: bboxOf(profile.bbox),
              },
            }))
          }
        >
          {t("showOnMap")}
        </button>
        <button
          className="btn"
          disabled={saved}
          onClick={() =>
            update((s) => ({
              ...s,
              shortlist: [
                ...s.shortlist,
                { code: profile.lsoa21cd, name: title, centre: profile.centre },
              ],
            }))
          }
        >
          {saved ? t("inShortlist") : t("addToShortlist")}
        </button>
        <CopyLink code={profile.lsoa21cd} />
      </div>
      <Muted>{t("note")}</Muted>
    </Card>
  );
}
