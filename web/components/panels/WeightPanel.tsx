"use client";

import { useTranslations } from "next-intl";

import { useData, useLiveability } from "@/components/AppData";
import { ProfileBox } from "@/components/ProfileBox";
import { useLabels } from "@/lib/labels";
import { themeVar } from "@/lib/palette";
import { PROFILE_OPTION, isTweaked, profileState, saveProfile, useSavedProfile } from "@/lib/profileStore";
import { type CompareWithin, effectiveWeights } from "@/lib/state";

/** A sentence about yourself or a preset to start from, then one slider per theme.
 * Moving a slider recolours the map at once, and the assistant sees the new weights on
 * its next turn. */
export function WeightPanel() {
  const { manifest } = useData();
  const { state, update } = useLiveability();
  const t = useTranslations("Weights");
  const labels = useLabels();
  const saved = useSavedProfile();
  if (!manifest) return null;
  const weights = effectiveWeights(manifest, state).themes;
  const preset = manifest.presets[state.preset];
  const active = state.profile;
  const tweaked = active ? (saved ? isTweaked(saved, state) : Object.keys(state.theme_weights).length > 0) : false;
  const canReset = active ? tweaked : Object.keys(state.theme_weights).length > 0;

  const reset = () =>
    update((s) =>
      s.profile && saved
        ? { ...s, theme_weights: { ...saved.theme_weights }, indicator_weights: { ...saved.indicator_weights } }
        : { ...s, theme_weights: {} },
    );

  return (
    <div className="space-y-3 p-3 text-sm" data-testid="weights">
      <div>
        <span className="mb-1 block text-xs font-medium text-[var(--text-secondary)]">{t("describeYourself")}</span>
        <ProfileBox />
      </div>

      <label className="block">
        <span className="mb-1 block text-xs font-medium text-[var(--text-secondary)]">{t("startFromPreset")}</span>
        <select
          className="input w-full"
          value={active ? PROFILE_OPTION : state.preset}
          data-testid="preset-select"
          onChange={(e) => {
            const value = e.target.value;
            if (value === PROFILE_OPTION) {
              if (saved) update((s) => ({ ...s, ...profileState(saved) }));
              return;
            }
            update((s) => ({ ...s, preset: value, theme_weights: {}, indicator_weights: {}, profile: null }));
          }}
        >
          {(active || saved) && (
            <option value={PROFILE_OPTION}>{t("myProfile", { name: active?.name ?? saved?.name ?? "" })}</option>
          )}
          {Object.keys(manifest.presets).map((id) => (
            <option key={id} value={id}>{labels.preset(id)}</option>
          ))}
        </select>
        {active ? (
          <span className="mt-1 block text-xs text-[var(--text-muted)]" data-testid="profile-card">
            {active.rationale || saved?.rationale || labels.presetDescription(state.preset)}
            {(saved?.engine ?? "model") === "rules" && <> {t("profileByRules")}</>}{" "}
            <button
              className="underline"
              data-testid="profile-clear"
              onClick={() => {
                saveProfile(null);
                update((s) => ({ ...s, theme_weights: {}, indicator_weights: {}, profile: null }));
              }}
            >
              {t("clearProfile")}
            </button>
          </span>
        ) : (
          preset && <span className="mt-1 block text-xs text-[var(--text-muted)]">{labels.presetDescription(state.preset)}</span>
        )}
      </label>

      <div>
        <div className="mb-1 flex items-baseline justify-between">
          <span className="text-xs font-medium text-[var(--text-secondary)]">{t("howMuch")}</span>
          {canReset && (
            <button className="text-xs underline" data-testid="weights-reset" onClick={reset}>
              {t("reset")}
            </button>
          )}
        </div>
        {Object.keys(manifest.themes).map((theme) => (
          <label key={theme} className="grid grid-cols-[8.5rem_1fr_2rem] items-center gap-2 py-1">
            <span className="flex min-w-0 items-center gap-1.5 text-xs" title={`${labels.theme(theme)}: ${labels.themeDescription(theme)}`}>
              <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: themeVar(theme) }} aria-hidden />
              <span className="truncate">{labels.theme(theme)}</span>
            </span>
            <input
              type="range"
              style={{ accentColor: themeVar(theme) }}
              min={0}
              max={3}
              step={0.25}
              value={weights[theme] ?? 0}
              aria-label={t("weightOf", { theme: labels.theme(theme) })}
              data-testid={`weight-${theme}`}
              onChange={(e) =>
                update((s) => ({ ...s, theme_weights: { ...s.theme_weights, [theme]: Number(e.target.value) } }))
              }
            />
            <span className="text-right text-xs tabular-nums">{(weights[theme] ?? 0).toFixed(2).replace(/\.?0+$/, "")}</span>
          </label>
        ))}
      </div>

      <label className="block">
        <span className="mb-1 block text-xs font-medium text-[var(--text-secondary)]">{t("colourBy")}</span>
        <select
          className="input w-full"
          value={state.map.layer}
          data-testid="layer-select"
          onChange={(e) => update((s) => ({ ...s, map: { ...s.map, layer: e.target.value } }))}
        >
          <option value="overall">{t("overall")}</option>
          <optgroup label={t("themes")}>
            {Object.keys(manifest.themes).map((id) => <option key={id} value={`theme:${id}`}>{labels.theme(id)}</option>)}
          </optgroup>
          <optgroup label={t("indicators")}>
            {manifest.indicators
              .filter((i) => manifest.scored_indicators.includes(i.id))
              .map((i) => <option key={i.id} value={`indicator:${i.id}`}>{labels.indicator(i.id)}</option>)}
          </optgroup>
          {(manifest.context_indicators?.length ?? 0) > 0 && (
            <optgroup label={t("contextIndicators")}>
              {manifest.indicators
                .filter((i) => manifest.context_indicators?.includes(i.id))
                .map((i) => <option key={i.id} value={`indicator:${i.id}`}>{labels.indicator(i.id)}</option>)}
            </optgroup>
          )}
        </select>
      </label>

      <label className="block text-xs">
        <span className="font-medium">{t("compareAgainst")}</span>
        <select
          className="input mt-1 w-full"
          data-testid="compare-within"
          value={state.compare_within}
          onChange={(e) => {
            const compare_within = e.target.value as CompareWithin;
            update((s) => ({
              ...s,
              compare_within,
              compare_within_urban_rural: compare_within === "urban_rural",
            }));
          }}
        >
          <option value="uk">{t("compare.uk")}</option>
          {(manifest.geography?.active ?? []).length > 1 && <option value="nation">{t("compare.nation")}</option>}
          <option value="urban_rural">{t("compare.urban_rural")}</option>
        </select>
        <span className="mt-1 block text-[var(--text-muted)]">{t(`compareHint.${state.compare_within}`)}</span>
      </label>
    </div>
  );
}
