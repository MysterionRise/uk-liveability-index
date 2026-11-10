import { type APIResponse, expect, test } from "@playwright/test";

/**
 * Security regression checks: what the proxy (docker/Caddyfile) gained in the v0.7.1
 * hardening pass. The main suite's dev servers have no proxy in front, so this runs only
 * against a running Docker stack (docker.yml on pull requests that touch the images,
 * release-smoke.yml on a release):
 *   LIX_E2E_STACK=docker npx playwright test security
 * No browser and no model key: every body posted here is one the app turns away before
 * the assistant runs, so the checks cost nothing on a stack that has a key. That the proxy
 * is published on loopback only cannot be seen from inside a test; docker.yml checks
 * `docker compose port proxy 8080` for it.
 */
test.skip(
  process.env.LIX_E2E_STACK !== "docker",
  "proxy checks: set LIX_E2E_STACK=docker with the stack running",
);

const RUN = "/api/copilotkit/agent/default/run";
const REFUSAL = "cross-site request refused";

// The two POSTs a hostile page would aim at: the assistant (this server's key) and the
// analyst's SQL. An empty run fails the relay's validation; the query reads no table
const POSTS = [
  { path: RUN, data: {} },
  { path: "/api/v1/sql", data: { query: "SELECT 1 AS one" } },
];

const refused = async (response: APIResponse) =>
  response.status() === 403 && (await response.text()).includes(REFUSAL);

/** A policy's directives by name, each with its list of sources. */
function directives(policy: string): Record<string, string[]> {
  return Object.fromEntries(
    policy
      .split(";")
      .map((directive) => directive.trim().split(/\s+/))
      .filter(([name]) => name)
      .map(([name, ...sources]) => [name, sources]),
  );
}

test("a POST from another site is refused", async ({ request, baseURL }) => {
  const own = new URL(baseURL!);
  const foreign = [
    "https://evil.example",
    // Names that contain this host's without being it: a check on the end or the start
    // of the Origin alone would let one of them through
    `${own.protocol}//evil.${own.host}`,
    `${own.origin}.evil.example`,
    // What a sandboxed frame and a file: page send
    "null",
  ];
  for (const { path, data } of POSTS) {
    for (const origin of foreign) {
      const response = await request.post(path, {
        data,
        headers: { Origin: origin },
      });
      expect(response.status(), `${path} from ${origin}`).toBe(403);
      expect(await response.text()).toContain(REFUSAL);
    }
  }
});

test("the app's own pages, and clients that send no Origin, are not refused", async ({
  request,
  baseURL,
}) => {
  const callers = {
    "the stack's own origin": { Origin: new URL(baseURL!).origin },
    "no Origin (curl, MCP clients)": {} as Record<string, string>,
  };
  for (const [caller, headers] of Object.entries(callers)) {
    for (const { path, data } of POSTS) {
      const response = await request.post(path, { data, headers });
      // The app may still turn the body away (the empty run is a 400): what must not
      // come back is the proxy's refusal
      expect(await refused(response), `${path} with ${caller}`).toBe(false);
    }
    const sql = await request.post("/api/v1/sql", {
      data: { query: "SELECT 1 AS one" },
      headers,
    });
    expect(sql.status(), `SQL with ${caller}`).toBe(200);
    expect((await sql.json()).rows).toEqual([[1]]);
  }
});

test("replies carry the content security policy and the hardening headers", async ({
  request,
}) => {
  const page = await request.get("/");
  expect(page.status()).toBe(200);
  const headers = page.headers();
  expect(headers["x-frame-options"]).toMatch(/^(SAMEORIGIN|DENY)$/i);
  // Any of the policies that keep the path and query from another site
  expect(headers["referrer-policy"]).toMatch(
    /^(no-referrer|same-origin|strict-origin|strict-origin-when-cross-origin)$/,
  );

  const policy = directives(headers["content-security-policy"] ?? "");
  expect(policy["default-src"]).toEqual(["'self'"]);
  // No source that means "any host": a wildcard, or a bare network scheme
  for (const [name, sources] of Object.entries(policy)) {
    for (const source of sources) {
      expect(source, `${name} ${source}`).not.toContain("*");
      expect(source, `${name} ${source}`).not.toMatch(/^(https?|wss?):$/);
    }
  }
  // What the policy is for: an image from another site (an injected reply could ask for
  // one, with the conversation in its URL) is never fetched
  expect(policy["img-src"]).toBeDefined();
  for (const source of policy["img-src"]) {
    expect(["'self'", "data:", "blob:"]).toContain(source);
  }

  // The headers are set for every reply: the API's and the data files' as well
  for (const path of ["/", "/health", "/data/manifest.json"]) {
    const response = await request.get(path);
    expect(response.headers()["x-content-type-options"], path).toBe("nosniff");
  }
});

test("a 12 MB body to the assistant's relay gets 413", async ({ request }) => {
  // The proxy caps a body at 9 MB; the web app used to read one whole before relaying it
  const response = await request.post(RUN, {
    headers: { "Content-Type": "application/json" },
    data: Buffer.alloc(12 * 1024 * 1024, "a"),
  });
  expect(response.status()).toBe(413);
});

test("the API's own assistant endpoint is not published", async ({
  request,
}) => {
  // The API serves the assistant at /agent for the web app's relay, inside the compose
  // network. The proxy routes only /api/v1, /health and /mcp to the API, so from outside
  // the path belongs to the web app, which has no such page
  expect((await request.get("/agent")).status()).toBe(404);
  expect((await request.post("/agent", { data: {} })).status()).toBe(404);
});
