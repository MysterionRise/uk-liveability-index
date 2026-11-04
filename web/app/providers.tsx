"use client";

import { CopilotKit } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";

import { DataProvider, LiveabilityStateProvider } from "@/components/AppData";
import { GenerativeUI } from "@/components/genui/registry";
import { LocaleProvider } from "@/components/LocaleProvider";
import { ThemeProvider } from "@/components/ThemeProvider";

/** CopilotKit talks to our Next route (/api/copilotkit), which relays to the Python agent. */
export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <LocaleProvider>
        {/* The route serves the multi-route (REST) transport. Left on "auto", a GET /info
            that failed for any reason made the client retry with a single-route envelope
            the route cannot serve, and the runtime logged it (#231). */}
        {/* CopilotKit's developer console (an English toast with a stack trace when the
            API is down) is on by default on localhost, which is where everyone self-hosts:
            the side panel says it instead, in the page's language (v0.7.1 hardening) */}
        <CopilotKit
          runtimeUrl="/api/copilotkit"
          enableInspector={false}
          useSingleEndpoint={false}
          showDevConsole={false}
        >
          <DataProvider>
            <LiveabilityStateProvider>
              <GenerativeUI />
              {children}
            </LiveabilityStateProvider>
          </DataProvider>
        </CopilotKit>
      </LocaleProvider>
    </ThemeProvider>
  );
}
