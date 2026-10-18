"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { useLiveability } from "@/components/AppData";
import { ApiError, generateProfile } from "@/lib/api";
import { profileState, saveProfile, savedFromProfile } from "@/lib/profileStore";

/** "Describe yourself": a sentence becomes a named weighting profile, kept in this
 * browser and applied to the sliders and the map. The welcome card shows it compact. */
export function ProfileBox({ compact = false, onDone }: { compact?: boolean; onDone?: () => void }) {
  const { update } = useLiveability();
  const t = useTranslations("Weights");
  const locale = useLocale();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = text.trim().length >= 3 && !busy;

  const build = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const description = text.trim();
      const profile = await generateProfile(description, locale);
      const saved = savedFromProfile(profile, description);
      saveProfile(saved);
      update((s) => ({ ...s, ...profileState(saved) }));
      onDone?.();
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setError(status === 429 ? t("profileBusy") : t("profileError", { detail: String((e as Error).message ?? e) }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid={compact ? "profile-box-compact" : "profile-box"}>
      <textarea
        className={`input w-full text-xs ${compact ? "h-12" : "h-16"}`}
        value={text}
        placeholder={t("describePlaceholder")}
        aria-label={t("describeYourself")}
        maxLength={500}
        data-testid="profile-input"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void build();
          }
        }}
      />
      <button className="btn mt-1 text-xs" disabled={!ready} data-testid="profile-build" onClick={() => void build()}>
        {busy ? t("building") : t("buildProfile")}
      </button>
      {error && (
        <p className="mt-1 text-xs text-[var(--critical)]" role="alert" data-testid="profile-error">
          ⚠ {error}
        </p>
      )}
    </div>
  );
}
