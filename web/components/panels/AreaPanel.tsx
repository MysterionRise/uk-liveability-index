"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { useLiveability } from "@/components/AppData";
import { AreaProfileCard } from "@/components/genui/AreaProfileCard";
import { fetchProfile } from "@/lib/api";
import type { AreaProfile } from "@/lib/contracts.gen";

interface Loaded {
  key: string;
  profile?: AreaProfile;
  error?: string;
}

/** The area selected on the map (or by the assistant), fetched for the current preset. */
export function AreaPanel() {
  const { state } = useLiveability();
  const t = useTranslations("AreaPanel");
  const locale = useLocale();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const code = state.map.selected;
  const weightsKey = JSON.stringify(state.theme_weights ?? {});
  const multipliersKey = JSON.stringify(state.indicator_weights ?? {});
  const key = `${code}|${state.preset}|${locale}|${weightsKey}|${multipliersKey}`;

  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    const themeWeights = JSON.parse(weightsKey) as Record<string, number>;
    const indicatorWeights = JSON.parse(multipliersKey) as Record<string, number>;
    fetchProfile(code, state.preset, locale, themeWeights, indicatorWeights)
      .then((profile) => !cancelled && setLoaded({ key, profile }))
      .catch(
        (e) => !cancelled && setLoaded({ key, error: String(e.message ?? e) }),
      );
    return () => {
      cancelled = true;
    };
  }, [code, state.preset, key, locale, weightsKey, multipliersKey]);

  if (!code) {
    return (
      <p className="p-4 text-sm text-[var(--text-secondary)]">{t("empty")}</p>
    );
  }
  // Results for a previous selection are ignored until the new one arrives
  if (!loaded || loaded.key !== key)
    return (
      <p className="p-4 text-sm text-[var(--text-muted)]">{t("loading")}</p>
    );
  if (loaded.error)
    return (
      <p className="p-4 text-sm text-[var(--text-secondary)]">
        {t("error", { error: loaded.error })}
      </p>
    );
  return (
    <div className="p-2">
      <AreaProfileCard profile={loaded.profile!} />
    </div>
  );
}
