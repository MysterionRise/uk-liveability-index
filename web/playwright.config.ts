import { defineConfig } from "@playwright/test";

// Runs against the real local build (data/serve) with the scripted assistant (no API key),
// starting the dev servers. LIX_E2E_STACK=docker tests the running docker compose stack
// instead (make gif, rehearsals, the release smoke test).
const external = process.env.LIX_E2E_STACK === "docker";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  // No retries: a retry turns a real race into a green run (#252 failed one run in seven
  // for a week). A failure keeps its trace instead, and e2e-soak.yml repeats the suite.
  retries: 0,
  use: {
    baseURL: process.env.LIX_BASE_URL ?? "http://localhost:3000",
    viewport: { width: 1400, height: 900 },
    trace: "retain-on-failure",
  },
  // Both servers' output goes to the test log: without stdout, the dev server's compile
  // lines and the API's request log were invisible when a case failed in CI
  webServer: external ? undefined : [
    {
      command: "cd .. && LIX_MODEL=test uv run lix-api",
      url: "http://localhost:8000/health",
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "npm run dev",
      url: "http://localhost:3000",
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
