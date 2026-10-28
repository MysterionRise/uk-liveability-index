"use client";

import { useTranslations } from "next-intl";
import { useEffect, useSyncExternalStore } from "react";

import { useData, useLiveability } from "@/components/AppData";
import { usePanel } from "@/components/PanelContext";
import { ProfileBox } from "@/components/ProfileBox";
import { useCoverageIn } from "@/lib/copy";
import { useLabels } from "@/lib/labels";

const KEY = "lix.welcomed";
const listeners = new Set<() => void>();

function readWelcomed(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return true; // storage blocked: don't nag on every visit
  }
}

/** Whether the welcome card has been dismissed (true on the server, so nothing flashes). */
export function useWelcomed(): boolean {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    readWelcomed,
    () => true,
  );
}

function dismiss() {
  try {
    localStorage.setItem(KEY, "1");
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

// Persona labels live in the catalogue, so the card can show before the manifest
// arrives (it is the largest text on a first visit) and in the interface language
const PERSONAS = ["family", "young_professional", "retiree", "commuter", "balanced"] as const;

/** First visit: pick who you are, or say it in a sentence (sets the weights), and how
 * to read the map. */
export function Onboarding() {
  const { manifest } = useData();
  const { update } = useLiveability();
  const { setTab } = usePanel();
  const t = useTranslations("Onboarding");
  const coverageIn = useCoverageIn();
  const labels = useLabels();
  const welcomed = useWelcomed();
  // Escape closes the dialog, as a dialog should (#188); while it is open, Tab cycles
  // within it, and closing it hands the focus back to where it was (#216)
  useEffect(() => {
    if (welcomed) return;
    const dialog = () => document.querySelector<HTMLElement>('[data-testid="onboarding"]');
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        dismiss();
        return;
      }
      const box = dialog();
      if (e.key !== "Tab" || !box) return;
      const items = Array.from(
        box.querySelectorAll<HTMLElement>("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"),
      ).filter((el) => !el.hasAttribute("disabled"));
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const inside = box.contains(document.activeElement);
      if (e.shiftKey && (document.activeElement === first || !inside)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !inside)) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    // A dialog takes the focus when it opens: the first persona, not the page behind
    dialog()?.querySelector<HTMLElement>("button")?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      const back =
        before && before !== document.body && document.contains(before)
          ? before
          : document.querySelector<HTMLElement>('[data-testid="search"] input');
      back?.focus();
    };
  }, [welcomed]);
  if (welcomed) return null;
  const presets = PERSONAS.filter((p) => !manifest || manifest.presets[p]);

  return (
    <div
      className="absolute left-3 top-3 z-20 w-[min(22rem,calc(100%-1.5rem))] rounded-lg border border-[var(--border)] bg-[var(--surface-1)] p-4 shadow-lg"
      role="dialog"
      aria-modal="true"
      aria-labelledby="welcome-title"
      data-testid="onboarding"
    >
      <h2 id="welcome-title" className="text-sm font-semibold">
        {t("title")}
      </h2>
      <p className="mt-1 text-xs text-[var(--text-secondary)]">{t("blurb", { coverageIn })}</p>
      <p className="mt-3 text-xs font-medium text-[var(--text-secondary)]">{t("whoIsLooking")}</p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {presets.map((id) => (
          <button
            key={id}
            className="btn text-xs"
            data-testid={`persona-${id}`}
            title={manifest ? labels.presetDescription(id) : undefined}
            onClick={() => {
              update((s) => ({ ...s, preset: id, theme_weights: {}, indicator_weights: {}, profile: null }));
              dismiss();
            }}
          >
            {t(`personas.${id}`)}
          </button>
        ))}
      </div>
      <p className="mt-3 text-xs font-medium text-[var(--text-secondary)]">{t("orDescribe")}</p>
      <div className="mt-1.5">
        <ProfileBox
          compact
          onDone={() => {
            dismiss();
            setTab("weights");
          }}
        />
      </div>
      <button className="mt-3 text-xs text-[var(--text-muted)] underline" onClick={dismiss}>
        {t("skip")}
      </button>
    </div>
  );
}
