"use client";

import { CopilotChat, useConfigureSuggestions } from "@copilotkit/react-core/v2";
import { useTranslations } from "next-intl";
import { useEffect, useRef } from "react";

import { useLiveability } from "@/components/AppData";
import { useLocaleChoice } from "@/components/LocaleProvider";
import { type Tab, usePanel } from "@/components/PanelContext";
import { AnalystPanel } from "@/components/panels/AnalystPanel";
import { AreaPanel } from "@/components/panels/AreaPanel";
import { ShortlistPanel } from "@/components/panels/ShortlistPanel";
import { WeightPanel } from "@/components/panels/WeightPanel";
import { useCoverageName } from "@/lib/copy";

const SUGGESTION_KEYS = ["family", "pubs", "compare", "explain"] as const;

export function SidePanel() {
  const t = useTranslations("SidePanel");
  const coverage = useCoverageName();
  const { state, update } = useLiveability();
  const { tab, setTab } = usePanel();
  const { locale } = useLocaleChoice();

  // The assistant replies in the page's language: it reads it from the shared state
  useEffect(() => {
    update((s) => (s.locale === locale ? s : { ...s, locale }));
  }, [locale, update]);

  const suggestions = SUGGESTION_KEYS.map((k) => ({
    title: t(`suggestions.${k}Title`),
    message: t(`suggestions.${k}Message`),
  }));
  useConfigureSuggestions({ suggestions, available: "before-first-message" }, [suggestions]);

  // Selecting an area (on the map or from a list) opens its profile, unless chatting.
  // The tab lives in a parent context, so this runs after render, not during it.
  const selected = state.map.selected;
  const seenSelected = useRef(selected);
  useEffect(() => {
    if (selected === seenSelected.current) return;
    seenSelected.current = selected;
    if (selected && tab !== "assistant") setTab("area");
  }, [selected, tab, setTab]);

  const tabs: [Tab, string][] = [
    ["assistant", t("tabs.assistant")],
    ["weights", t("tabs.weights")],
    ["area", t("tabs.area")],
    ["shortlist", state.shortlist.length ? t("shortlistCount", { count: state.shortlist.length }) : t("tabs.shortlist")],
    ...(state.mode === "analyst" ? [["analyst", t("tabs.analyst")] as [Tab, string]] : []),
  ];

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-[var(--border)] bg-[var(--surface-1)]">
      <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-[var(--border)] px-2">
        <div
          className="flex items-center gap-1"
          role="tablist"
          aria-label={t("panels")}
          onKeyDown={(e) => {
            // Arrow keys move between tabs (WAI-ARIA tabs pattern)
            if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
            const ids = tabs.map(([id]) => id);
            const next = ids[(ids.indexOf(tab) + (e.key === "ArrowRight" ? 1 : ids.length - 1)) % ids.length];
            setTab(next);
            document.getElementById(`tab-${next}`)?.focus();
          }}
        >
          {tabs.map(([id, label]) => (
            <button
              key={id}
              id={`tab-${id}`}
              role="tab"
              aria-selected={tab === id}
              aria-controls={`panel-${id}`}
              tabIndex={tab === id ? 0 : -1}
              data-testid={`tab-${id}`}
              onClick={() => setTab(id)}
              className={`whitespace-nowrap border-b-2 px-2.5 py-2 text-sm ${
                tab === id
                  ? "border-[var(--text-primary)] font-medium text-[var(--text-primary)]"
                  : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {/* Outside the tablist: a tablist may only contain tabs */}
        <label
          className="ml-auto flex shrink-0 items-center gap-1.5 py-2 pl-2 text-xs text-[var(--text-secondary)]"
          title={t("analystTitle")}
        >
          <input
            type="checkbox"
            data-testid="analyst-toggle"
            checked={state.mode === "analyst"}
            onChange={(e) => {
              const analyst = e.target.checked;
              update((s) => ({ ...s, mode: analyst ? "analyst" : "consumer" }));
              if (!analyst && tab === "analyst") setTab("assistant");
            }}
          />
          {/* Once on, the Analyst tab names it; the word would crowd the tabs off a narrow panel */}
          <span className={state.mode === "analyst" ? "sr-only" : ""}>{t("tabs.analyst")}</span>
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* Keep the chat mounted so the conversation survives tab switches */}
        <div className={tab === "assistant" ? "h-full" : "hidden"} data-testid="chat" id="panel-assistant" role="tabpanel">
          <CopilotChat
            className="h-full"
            labels={{
              assistantMessageToolbarCopyMessageLabel: t("copyMessage"),
              userMessageToolbarCopyMessageLabel: t("copyMessage"),
              chatInputPlaceholder: t("chatPlaceholder"),
              welcomeMessageText: t("chatWelcome"),
              chatDisclaimerText: t("chatDisclaimer"),
            }}
            // CopilotKit's icon buttons have no accessible names of their own
            input={{ sendButton: { "aria-label": t("send") }, addMenuButton: { "aria-label": t("moreOptions") } }}
          />
        </div>
        {tab !== "assistant" && (
          <div id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`}>
            {tab === "weights" && <WeightPanel />}
            {tab === "area" && <AreaPanel />}
            {tab === "shortlist" && <ShortlistPanel />}
            {tab === "analyst" && <AnalystPanel />}
          </div>
        )}
      </div>
    </aside>
  );
}
