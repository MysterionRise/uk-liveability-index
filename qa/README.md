# Test passes

Before a release, several testers (people or coding agents) each try to break one part of
the release candidate. This folder is where a pass lives while it runs:

```
qa/
  README.md            this file (the only one in git)
  v0.7.2/
    briefs/            one brief per tester, written from the template below
    findings/          one file per tester: what they did, what they found
    evidence/          screenshots, transcripts, logs, scripts
    evals/             the release's eval runs, one folder per model and run
```

Everything but this file is ignored by git: findings quote model replies, logs and local
paths, and none of that belongs in the history. What a pass found goes into the tracker
instead: one issue titled "Test pass vX.Y.Z" with each tester's findings as a comment, and
one issue per bug or gap, with its diagnosis and a fix design.

Checks that can be scripted are not a tester's job. If a pass finds the same kind of thing
twice, it becomes a test or a CI check (`lix docs --check` holds the docs' numbers to the
repository; `docker.yml` builds the images and runs the smoke and security specs;
`e2e-soak.yml` repeats the browser suite; `evals.yml` repeats the assistant evals).

## A tester's brief

Copy this, fill it in, and give it to one tester. A brief that cannot say what "broken"
would look like is not ready.

```markdown
# <Tester name>: <scope in one line>

**Release candidate:** vX.Y.Z-rc.N (images `…-api:X.Y.Z-rc.N`, `…-web:X.Y.Z-rc.N`), data release data-YYYY-MM
**What changed in this release:** the CHANGELOG's Unreleased section, and for this scope: <issues and PRs>

## Try to break
- <the three to six things most likely to be wrong, each as something to do>

## Out of scope
- <what another tester covers; what is known and already filed, with issue numbers>

## Environment
- Your own clone or checkout, your own ports (see below). Write only under `qa/vX.Y.Z/`.
- <stack: RC images with `LIX_VERSION=X.Y.Z-rc.N make up`, or a dev stack, or none>
- <model: none, the scripted one (`LIX_MODEL=test`), or a real one and its spending cap>

## Report (qa/vX.Y.Z/findings/<name>.md)
For each finding: what you did (exact steps or the command), what happened, what should
have happened, how sure you are that it is a bug (reproduced N of M), and the evidence
file. Then: what you tried that held, and what you could not test and why.
A finding without a reproduction is a note, and say so.
```

## Environment notes that have cost time before

- **Ports.** `make up` publishes the proxy on 3000 and nothing else. Two stacks on one
  machine need two compose project names and two ports
  (`COMPOSE_PROJECT_NAME=lix-qa-a LIX_PORT=3010 make up`: in the compose file `LIX_PORT` is
  the proxy's published port); a dev API takes `LIX_PORT`
  and its web server `-p`.
- **A dev stack for the browser suite** when the default ports are taken:
  `LIX_PORT=8010 LIX_CORS_ORIGINS=http://localhost:3005 LIX_MODEL=test LIX_DATA_DIR=$PWD/fixtures/demo uv run lix-api`,
  `NEXT_PUBLIC_API_URL=http://localhost:8010 AGENT_URL=http://localhost:8010/agent npm run dev -- -p 3005`
  (in `web/`), then
  `LIX_E2E_STACK=docker LIX_BASE_URL=http://localhost:3005 npx playwright test app --workers=1`.
- **`uv run` from the repository root**, not from `api/` or `pipeline/`: run from a member
  it syncs the environment to that member alone.
- **Node 24** for everything under `web/`. After a lockfile change,
  `npx playwright install chromium` before the browser suite, or every case fails at launch.
- **A real model costs money.** Override the model for a pass
  (`LIX_MODEL=openrouter:anthropic/claude-haiku-5.5`), keep `LIX_DAILY_BUDGET` low, and
  say in the findings what was spent. Never print or copy a key into a findings file.
- **A flaky check needs a failing test first.** A green rerun proves nothing: force the
  bad ordering, see it fail, then fix (#252). The same goes for an instruction added to the
  assistant: run the eval case on the old prompt first, and keep the line only if the
  case fails without it.
- **A browser test cannot cancel what has not started.** CopilotKit's stop is a request to
  the runtime, so a test that holds the run request in the browser sees the reply arrive
  anyway: assert on the request (#236).
