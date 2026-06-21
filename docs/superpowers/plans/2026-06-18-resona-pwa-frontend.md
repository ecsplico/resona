# Resona PWA Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Resona Nuxt 4 PWA (record, upload, live, history, edit, export) on the Hearth design system, talking to Directus (`:7700`) for auth/data/files and to resona-api (`:7710`) for live streaming.

**Architecture:** Standalone Nuxt 4 app in its **own git repo** at `/home/matthias/workBench/resona-pwa` (NOT committed into the resona Python monorepo). Two transcription paths: **batch** (upload audio to Directus → create `recordings` row `status=pending` → the `directus-transcribe` worker fills it in → PWA polls) and **live** (PWA opens `WS /v1/listen` on resona-api directly, then writes the `recordings` + `transcripts` rows itself as `source=live, status=done`). All Directus access is via `@directus/sdk`; live audio is 16 kHz mono PCM16 over WebSocket.

**Tech Stack:** Nuxt 4.4.x (`app/` dir), bun, TypeScript, Tailwind v4 (`@tailwindcss/vite`), `@nuxt/fonts` (Fraunces / Instrument Sans / JetBrains Mono), `@vueuse/nuxt` (`useWebSocket`) + native `MediaRecorder`, `@directus/sdk` v22 (`authentication('json')` + `rest()`), `@vite-pwa/nuxt` v1.1, `@nuxt/eslint` (stylistic: tabs, double quotes, semis, trailing commas), Vitest 4 + `@nuxt/test-utils` 4 for TDD.

---

## Stack decisions & deviations from spec (CONFIRM AT APPROVAL)

These differ from `docs/superpowers/specs/2026-06-17-pwa-directus-platform-design.md` §8/§11. They follow the user's `NUXT-TEMPLATE` house style and the Hearth research. Adjust before execution if undesired.

1. **No Nuxt UI.** The spec named Nuxt UI for primitives. Hearth is a fully bespoke kit with exact pixel/colour values; theming Nuxt UI to it is more friction than porting the components directly. We build Hearth as a small set of Vue components on plain Tailwind v4 + a CSS token layer. (Spec §9 already mandates porting Hearth components to Vue — this just drops the redundant Nuxt UI layer underneath.)
2. **Vitest, not Playwright, for the TDD loop.** The house template uses Playwright for crawl/visual regression. That doesn't fit per-task red-green TDD. We use Vitest 4 + `@nuxt/test-utils` 4 for composable/component unit tests (spec §11 explicitly asks for Vitest). Playwright visual regression can be added later as a non-TDD CI step.
3. **Package manager: bun** (house style), Nuxt 4.4.x with the native `app/` layout (house template emulates this via `compatibilityVersion: 4`; we use real Nuxt 4).
4. **Backend assumption:** Directus is reachable at `http://localhost:7700` with the `recordings`/`transcripts` collections and the "user" role/policy from `directus/bootstrap.md` already applied, and resona-api at `http://localhost:7710`. These are runtime config, not build-time deps. Tests mock both — no live backend needed to implement or test this plan.

---

## Environment & conventions (read before Task 1)

- **Repo root:** `/home/matthias/workBench/resona-pwa` — a fresh git repo, created in Task 1. All paths below are relative to it unless absolute.
- **Source dir:** Nuxt 4 puts app code under `app/`. Components auto-import from `app/components/`, composables from `app/composables/`.
- **ESLint stylistic:** tabs for indent, double quotes, semicolons required, trailing commas always-multiline. Match this in every file. (Code blocks in this plan use spaces for readability — convert to tabs when writing.)
- **Commit cadence:** one commit per task (after its tests pass), conventional-commit messages. End commit messages with the Co-Authored-By trailer per the monorepo convention.
- **Directus schema (TS):** `recordings` and `transcripts` shapes come from spec §5. Defined once in Task 6.
- **resona-api live frame contract** (from monorepo CLAUDE.md `streaming_routes.py` + `remote_live.py`):
  - Connect: `ws://<api>/v1/listen?encoding=linear16&sample_rate=16000&interim_results=true&language=<de>&engine=<name>&token=<RESONA_API_KEY>`. WebSocket can't set headers, so auth uses `?token=`.
  - Up: binary PCM frames (16 kHz mono **int16 little-endian**). (The gateway also accepts a `{type:"audio",data:<base64 int16>}` JSON frame, but binary is simplest from the browser.)
  - Down: Deepgram-style JSON. `{"type":"Results","channel":{"alternatives":[{"transcript":"..."}]},"is_final":bool}` and `{"type":"Metadata",...}`.
  - Finish: send `{"type":"CloseStream"}` then close.

---

## File structure

```
resona-pwa/
├─ nuxt.config.ts            # modules, pwa, runtimeConfig, fonts, eslint, tailwind vite plugin
├─ app.config.ts             # (minimal) app-level config
├─ vitest.config.ts          # nuxt test environment
├─ eslint.config.mjs         # withNuxt stylistic
├─ tsconfig.json
├─ package.json
├─ .env.example
├─ .gitignore
├─ public/                   # pwa icons (pwa-192, pwa-512, maskable-512), favicon
├─ app/
│  ├─ app.vue                # <NuxtLayout><NuxtPage/></NuxtLayout>
│  ├─ assets/css/
│  │  ├─ tokens.css          # Hearth :root design tokens (single source of truth)
│  │  └─ main.css            # @import tailwindcss; @import tokens; base element styles
│  ├─ components/hearth/     # ported Hearth kit
│  │  ├─ HButton.vue HChip.vue HIconButton.vue HField.vue
│  │  ├─ HCard.vue HListRow.vue HToggle.vue HAppBar.vue
│  │  ├─ HRecordFab.vue HRecordingSheet.vue HPlayerDock.vue
│  ├─ components/            # app-level (RecordingListItem.vue, StatusChip.vue, etc.)
│  ├─ composables/
│  │  ├─ useDirectus.ts      # client + auth
│  │  ├─ useRecordings.ts    # list/query
│  │  ├─ useRecording.ts     # single fetch + status poll
│  │  ├─ useRecorder.ts      # MediaRecorder + levels
│  │  ├─ useLive.ts          # WS /v1/listen + Deepgram parse + PCM encode
│  │  ├─ useExport.ts        # TXT/MD export + copy
│  │  └─ useOfflineQueue.ts  # IndexedDB queue + sync
│  ├─ middleware/
│  │  ├─ auth.global.ts      # gate everything except /login
│  ├─ layouts/default.vue
│  ├─ pages/
│  │  ├─ login.vue index.vue
│  │  ├─ recording/[id].vue  live.vue
│  ├─ types/directus.ts      # Schema, Recording, Transcript
│  └─ utils/pcm.ts           # float32 -> 16k int16 downsample/encode
└─ test/                     # vitest specs mirror app/ (or co-located *.spec.ts)
```

---

# Phase A — Scaffold & foundation

## Task 1: Initialize repo, Nuxt 4, tooling, smoke test

**Files:**
- Create: the whole `/home/matthias/workBench/resona-pwa` repo (package.json, nuxt.config.ts, tsconfig.json, eslint.config.mjs, vitest.config.ts, app/app.vue, .gitignore, .env.example)
- Test: `test/smoke.spec.ts`

- [ ] **Step 1: Create the project and git repo**

```bash
mkdir -p /home/matthias/workBench/resona-pwa
cd /home/matthias/workBench/resona-pwa
git init
bun init -y   # then overwrite package.json below
```

- [ ] **Step 2: Write `package.json`** (match house scripts; add test + deps)

```json
{
  "name": "resona-pwa",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "packageManager": "bun@1.3.14",
  "scripts": {
    "dev": "nuxt dev",
    "build": "nuxt build",
    "generate": "nuxt generate",
    "preview": "nuxt preview",
    "postinstall": "nuxt prepare",
    "typecheck": "nuxt typecheck",
    "lint": "eslint .",
    "lint:fix": "eslint . --fix",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@directus/sdk": "^22.0.0",
    "@nuxt/fonts": "^0.11.4",
    "@tailwindcss/vite": "^4.1.17",
    "@vite-pwa/nuxt": "^1.1.1",
    "@vueuse/nuxt": "^14.3.0",
    "nuxt": "^4.4.8",
    "tailwindcss": "^4.1.17",
    "vue": "^3.5.21",
    "vue-router": "^4.5.1"
  },
  "devDependencies": {
    "@nuxt/eslint": "^1.4.1",
    "@nuxt/test-utils": "^4.0.3",
    "@vue/test-utils": "^2.4.6",
    "@vueuse/core": "^14.3.0",
    "eslint": "^9.21.0",
    "happy-dom": "^15.0.0",
    "typescript": "^5.8.3",
    "vitest": "^4.1.9",
    "vue-tsc": "^2.2.10"
  }
}
```

- [ ] **Step 3: Write `nuxt.config.ts`**

```ts
import tailwindcss from "@tailwindcss/vite";

export default defineNuxtConfig({
  modules: ["@nuxt/eslint", "@nuxt/fonts", "@vueuse/nuxt", "@vite-pwa/nuxt"],
  devtools: { enabled: true },
  css: ["~/assets/css/main.css"],
  vite: { plugins: [tailwindcss()] },
  experimental: { typedPages: true },
  compatibilityDate: "2026-06-18",
  runtimeConfig: {
    public: {
      directusUrl: "http://localhost:7700",
      resonaApiUrl: "http://localhost:7710",
      resonaApiKey: "",
      defaultLanguage: "de",
      defaultProfile: "default",
      defaultEngine: "faster-whisper",
    },
  },
  app: {
    head: {
      charset: "utf-8",
      viewport: "width=device-width, initial-scale=1, viewport-fit=cover",
      title: "Resona",
    },
  },
  fonts: {
    families: [
      { name: "Fraunces", provider: "google" },
      { name: "Instrument Sans", provider: "google" },
      { name: "JetBrains Mono", provider: "google" },
    ],
  },
  eslint: {
    config: {
      stylistic: { semi: true, quotes: "double", indent: "tab", commaDangle: "always-multiline" },
    },
  },
  pwa: { registerType: "autoUpdate", manifest: false, devOptions: { enabled: false } }, // expanded in Task 21
});
```

(`runtimeConfig.public` values are overridden at runtime by `NUXT_PUBLIC_DIRECTUS_URL`, `NUXT_PUBLIC_RESONA_API_URL`, `NUXT_PUBLIC_RESONA_API_KEY`.)

- [ ] **Step 4: Write `eslint.config.mjs`, `vitest.config.ts`, `tsconfig.json`, `app/app.vue`, `.gitignore`, `.env.example`**

```js
// eslint.config.mjs
// @ts-check
import withNuxt from "./.nuxt/eslint.config.mjs";
export default withNuxt({ rules: { "vue/multi-word-component-names": "off" } });
```

```ts
// vitest.config.ts
import { defineVitestConfig } from "@nuxt/test-utils/config";
export default defineVitestConfig({ test: { environment: "nuxt" } });
```

```json
// tsconfig.json
{ "extends": "./.nuxt/tsconfig.json" }
```

```vue
<!-- app/app.vue -->
<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>
```

```
# .gitignore
node_modules
.nuxt
.output
dist
.env
*.log
.DS_Store
```

```
# .env.example
NUXT_PUBLIC_DIRECTUS_URL=http://localhost:7700
NUXT_PUBLIC_RESONA_API_URL=http://localhost:7710
NUXT_PUBLIC_RESONA_API_KEY=
NUXT_PUBLIC_DEFAULT_LANGUAGE=de
NUXT_PUBLIC_DEFAULT_PROFILE=default
NUXT_PUBLIC_DEFAULT_ENGINE=faster-whisper
```

- [ ] **Step 5: Install & generate Nuxt types**

Run: `cd /home/matthias/workBench/resona-pwa && bun install`
Expected: installs cleanly; `postinstall` runs `nuxt prepare` and creates `.nuxt/`.

- [ ] **Step 6: Write the failing smoke test** — `test/smoke.spec.ts`

```ts
import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import App from "~/app.vue";

describe("app", () => {
  it("mounts", async () => {
    const wrapper = await mountSuspended(App);
    expect(wrapper.html()).toBeTruthy();
  });
});
```

- [ ] **Step 7: Run the test**

Run: `bun run test`
Expected: PASS (app mounts). If the nuxt test env needs a default page, add a minimal `app/pages/index.vue` with `<template><div>Resona</div></template>` and re-run.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "chore: scaffold Nuxt 4 PWA (bun, tailwind v4, vitest)"
```

---

## Task 2: Hearth design tokens + fonts + base CSS

**Files:**
- Create: `app/assets/css/tokens.css`, `app/assets/css/main.css`
- Test: `test/tokens.spec.ts`

- [ ] **Step 1: Write `tokens.css`** — verbatim Hearth tokens (from spec §9 + Hearth extraction). Single source of truth.

```css
:root {
  /* palette */
  --cream:#FBF5EC; --cream-deep:#F4EBD9; --white:#FFFFFF;
  --ink:#2A1E16; --ink-soft:#4A3A2E; --ink-mute:#75655A;
  --tomato:#D96846; --tomato-deep:#B85032; --tomato-soft:#F3D1BE;
  --olive:#6B8E4E; --olive-deep:#4E6B38; --olive-soft:#C8D8B4;
  --butter:#E8A838; --butter-deep:#C48A1E; --butter-soft:#F5D88F;
  --crust:#B8916A; --crust-deep:#8B6944; --crust-soft:#E2D2BE;
  /* semantic */
  --bg:var(--cream); --bg-raised:var(--white); --bg-sunken:var(--cream-deep);
  --fg1:var(--ink); --fg2:var(--ink-soft); --fg3:var(--ink-mute); --fg-on-accent:var(--white);
  --accent:var(--tomato); --accent-hover:var(--tomato-deep); --accent-soft:var(--tomato-soft);
  --success:var(--olive); --warning:var(--butter); --danger:var(--tomato-deep);
  --border:rgba(139,105,68,0.18); --border-strong:rgba(139,105,68,0.40);
  --focus-ring:rgba(217,104,70,0.40);
  /* shadow */
  --shadow-soft:0 1px 2px rgba(88,52,28,0.06),0 2px 8px rgba(88,52,28,0.04);
  --shadow-lift:0 4px 12px rgba(88,52,28,0.10),0 8px 24px rgba(88,52,28,0.06);
  --shadow-deep:0 12px 32px rgba(88,52,28,0.14),0 4px 8px rgba(88,52,28,0.06);
  /* radii */
  --r-xs:4px; --r-sm:8px; --r-md:12px; --r-lg:20px; --r-full:999px;
  /* fonts */
  --font-display:"Fraunces","Cooper BT",Georgia,serif;
  --font-body:"Instrument Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  --font-mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
```

- [ ] **Step 2: Write `main.css`**

```css
@import "tailwindcss";
@import "./tokens.css";

@theme {
  --color-cream: #FBF5EC;
  --color-tomato: #D96846;
  --color-olive: #6B8E4E;
  --color-butter: #E8A838;
  --color-ink: #2A1E16;
  /* expose the Hearth palette to Tailwind utilities as needed */
}

html, body { background: var(--bg); color: var(--fg1); font-family: var(--font-body); }
.font-display { font-family: var(--font-display); }
.font-mono { font-family: var(--font-mono); }
```

- [ ] **Step 3: Write the failing test** — `test/tokens.spec.ts` (verifies tokens resolve on a mounted component)

```ts
import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { defineComponent, h } from "vue";

const Probe = defineComponent({ setup: () => () => h("div", { style: "color: var(--accent)" }, "x") });

describe("tokens", () => {
  it("accent token resolves to the tomato hex via getComputedStyle", async () => {
    const wrapper = await mountSuspended(Probe);
    const el = wrapper.element as HTMLElement;
    // token defined on :root; jsdom/happy-dom resolves custom props on the element
    const root = document.documentElement;
    root.style.setProperty("--accent", "#D96846");
    expect(getComputedStyle(root).getPropertyValue("--accent").trim()).toBe("#D96846");
  });
});
```

> Note: CSS-variable resolution in the test env is limited; this test documents intent and guards the token name. The real verification is visual (Step 5). Keep the test minimal and green.

- [ ] **Step 4: Run** `bun run test` → PASS.
- [ ] **Step 5: Visual check (manual)** — `bun run dev`, open `/`, confirm cream background + fonts load. Note in commit.
- [ ] **Step 6: Commit** — `git commit -am "feat: Hearth design tokens + base CSS"`

---

# Phase B — Hearth Vue component library

> Each component is a thin Vue port of the Hearth kit using the exact values from the extraction. Props are reconstructed (the source had none). TDD: render → assert variant classes/inline style → commit. Group small components per task to keep momentum.

## Task 3: HButton + HIconButton

**Files:** Create `app/components/hearth/HButton.vue`, `app/components/hearth/HIconButton.vue`; Test `test/hearth/HButton.spec.ts`.

- [ ] **Step 1: Failing test**

```ts
import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HButton from "~/components/hearth/HButton.vue";

describe("HButton", () => {
  it("renders the label slot", async () => {
    const w = await mountSuspended(HButton, { slots: { default: () => "Speichern" } });
    expect(w.text()).toContain("Speichern");
  });
  it("primary variant uses the accent background", async () => {
    const w = await mountSuspended(HButton, { props: { variant: "primary" } });
    expect(w.attributes("class")).toContain("h-btn--primary");
  });
  it("secondary variant uses the outline class", async () => {
    const w = await mountSuspended(HButton, { props: { variant: "secondary" } });
    expect(w.attributes("class")).toContain("h-btn--secondary");
  });
});
```

- [ ] **Step 2: Run** → FAIL (component missing).
- [ ] **Step 3: Implement `HButton.vue`** (variant `primary|secondary`, default primary; emits click; disabled state)

```vue
<script setup lang="ts">
defineProps<{ variant?: "primary" | "secondary"; disabled?: boolean }>();
</script>
<template>
  <button
    class="h-btn"
    :class="`h-btn--${variant ?? 'primary'}`"
    :disabled="disabled"
  >
    <slot />
  </button>
</template>
<style scoped>
.h-btn { border:none; border-radius:10px; padding:14px 16px; font-family:var(--font-body); font-weight:600; font-size:15px; cursor:pointer; }
.h-btn--primary { background:var(--accent); color:var(--fg-on-accent); }
.h-btn--secondary { background:transparent; color:var(--fg2); border:1.5px solid var(--border-strong); }
.h-btn:disabled { opacity:.5; cursor:default; }
</style>
```

- [ ] **Step 4: Implement `HIconButton.vue`** (props `size?: number = 38`, `tone?: "glass" | "cream" = "cream"`; round; slot for icon). glass = `rgba(255,255,255,.16)` white icon; cream = `var(--cream-deep)` + `var(--crust-deep)` icon.
- [ ] **Step 5: Run** → PASS. **Step 6: Commit** `feat: HButton + HIconButton`.

## Task 4: HChip + status→tone mapping

**Files:** Create `app/components/hearth/HChip.vue`, `app/components/StatusChip.vue`; Test `test/hearth/HChip.spec.ts`, `test/StatusChip.spec.ts`.

- [ ] **Step 1: Failing test for HChip** — `tone: success|warning|neutral|danger`, optional `dot`. Assert tone class + dot element present when `dot`.
- [ ] **Step 2:** Implement `HChip.vue` with the tone matrix (success: olive-soft/olive-deep; warning: butter-soft/#8B6200 + butter-deep dot; neutral: crust-soft/crust-deep; danger: tomato-soft/tomato-deep). Pill `padding:4px 9px; border-radius:999px; font:600 11px`; 6px leading dot when `dot`.
- [ ] **Step 3: Failing test for StatusChip** — maps recording status → HChip:
  `done→success "Fertig"`, `transcribing→warning "Transkribiert…" dot`, `pending→neutral "Wartet"`, `error→danger "Fehler"`.

```ts
import { mountSuspended } from "@nuxt/test-utils/runtime";
import StatusChip from "~/components/StatusChip.vue";
// it("maps done to a success chip labelled Fertig") -> assert text + tone
```

- [ ] **Step 4:** Implement `StatusChip.vue` (a `STATUS_TONE`/`STATUS_LABEL` map → `<HChip>`).
- [ ] **Step 5: Run** → PASS. **Step 6: Commit** `feat: HChip + StatusChip status mapping`.

## Task 5: HField + HToggle

**Files:** Create `app/components/hearth/HField.vue`, `HToggle.vue`; Test `test/hearth/HField.spec.ts`, `HToggle.spec.ts`.

- [ ] **Step 1: Failing test HField** — `v-model` (modelValue/update:modelValue), `label`, `focused` adds focus-ring class, optional leading icon slot, `mono` boolean renders value in `--font-mono`. Assert typing emits `update:modelValue`.
- [ ] **Step 2:** Implement `HField.vue` (input shell `1.5px var(--border-strong)`, focus → `border var(--accent)` + `0 0 0 3px var(--focus-ring)`).
- [ ] **Step 3: Failing test HToggle** — `v-model` boolean; clicking emits toggle; `on` class when true.
- [ ] **Step 4:** Implement `HToggle.vue` (track 50×30, knob 24, on=accent/right off=#D9CBB6/left).
- [ ] **Step 5: Run** → PASS. **Step 6: Commit** `feat: HField + HToggle`.

## Task 6: HCard + HListRow + HAppBar + Directus types

**Files:** Create `app/components/hearth/HCard.vue`, `HListRow.vue`, `HAppBar.vue`, `app/types/directus.ts`; Test `test/hearth/HListRow.spec.ts`.

- [ ] **Step 1: Write `app/types/directus.ts`** (no test; consumed by later tasks)

```ts
export interface Recording {
  id: string;
  user_created?: string;
  date_created?: string;
  title: string;
  audio_file?: string;
  duration_seconds?: number;
  language: string;
  profile: string;
  status: "pending" | "transcribing" | "done" | "error";
  error_message?: string | null;
  source: "batch" | "live";
}
export interface Transcript {
  id: string;
  recording: string;
  text: string;
  structured?: Record<string, unknown> | null;
  segments?: unknown[] | null;
  engine?: string;
  date_created?: string;
  date_updated?: string;
}
export interface Schema { recordings: Recording[]; transcripts: Transcript[]; }
```

- [ ] **Step 2: Failing test HListRow** — props `title`, `meta` (mono), `status`, optional `preview`, `selected`. Assert title text, a `StatusChip` rendered for the status, `selected` adds the tinted class, and a `select` event fires on click.
- [ ] **Step 3:** Implement `HCard.vue` (white, r12, `--shadow-soft`, `selected` → `#FCEFE8` + accent border), `HListRow.vue` (uses HCard + StatusChip + action slot), `HAppBar.vue` (`tone: dark|light`; dark = tomato bg white text, light = cream bg ink text; title Fraunces).
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: HCard, HListRow, HAppBar + Directus types`.

---

# Phase C — Directus integration & auth

## Task 7: useDirectus composable (client + auth)

**Files:** Create `app/composables/useDirectus.ts`; Test `test/useDirectus.spec.ts`.

- [ ] **Step 1: Failing test** — mock `@directus/sdk`. Assert `useDirectus()` builds a client from `runtimeConfig.public.directusUrl`, `login(email,password)` calls the SDK login, `logout()` clears auth, `isAuthenticated` reflects token presence.

```ts
import { describe, it, expect, vi } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";

vi.mock("@directus/sdk", () => {
  const client = {
    login: vi.fn().mockResolvedValue({ access_token: "t" }),
    logout: vi.fn().mockResolvedValue(undefined),
    getToken: vi.fn().mockResolvedValue("t"),
    request: vi.fn(),
    with: vi.fn().mockReturnThis(),
  };
  return {
    createDirectus: vi.fn(() => client),
    authentication: vi.fn(() => (c: unknown) => c),
    rest: vi.fn(() => (c: unknown) => c),
    readItems: vi.fn(), readItem: vi.fn(), createItem: vi.fn(),
    updateItem: vi.fn(), deleteItem: vi.fn(), uploadFiles: vi.fn(),
  };
});
// it: login() calls client.login with creds; isAuthenticated true after
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — singleton client via `createDirectus<Schema>(url).with(authentication("json")).with(rest())`; expose `client`, `login`, `logout`, `isAuthenticated` (ref), `restoreSession()` (try `getToken`). Use `useRuntimeConfig().public.directusUrl`. Build `assetUrl(fileId)` → `${directusUrl}/assets/${fileId}?access_token=${token}`.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: useDirectus (client + auth)`.

## Task 8: auth middleware + /login page

**Files:** Create `app/middleware/auth.global.ts`, `app/pages/login.vue`, `app/layouts/default.vue`; Test `test/auth.middleware.spec.ts`.

- [ ] **Step 1: Failing test** — with `useDirectus` mocked unauthenticated, navigating to `/` redirects to `/login`; authenticated → allowed; `/login` always allowed. Test the middleware function directly with stubbed `navigateTo`.
- [ ] **Step 2:** Implement `auth.global.ts` (allow `/login`; else if `!isAuthenticated` → `navigateTo("/login")`; call `restoreSession()` first on client).
- [ ] **Step 3:** Implement `login.vue` — Hearth login screen (app icon, Fraunces title, `HField` email/password, primary `HButton`, error text). On submit → `useDirectus().login()` → `navigateTo("/")`. `definePageMeta` not needed (global mw handles it; guard `/login` inside mw).
- [ ] **Step 4:** Implement `default.vue` layout (`<slot/>` inside an app shell container).
- [ ] **Step 5: Run** → PASS. **Step 6: Commit** `feat: auth middleware + login page`.

---

# Phase D — Recordings list & detail (batch read path)

## Task 9: useRecordings composable

**Files:** Create `app/composables/useRecordings.ts`; Test `test/useRecordings.spec.ts`.

- [ ] **Step 1: Failing test** — mock client `request` to return rows. `list({ search, since })` calls `readItems("recordings", { filter, sort:["-date_created"], limit })`. Assert search maps to a `title._icontains` filter and `since` to `date_created._gte`. Returns the rows.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — `useRecordings()` exposing `items` (ref), `pending`, `error`, `refresh`, and `list(opts)`. Build the Directus query; `user_created` is enforced server-side by the policy, so no client filter needed.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: useRecordings list/query`.

## Task 10: index.vue recordings list page

**Files:** Create `app/pages/index.vue`, `app/components/RecordingListItem.vue`; Test `test/index.page.spec.ts`.

- [ ] **Step 1: Failing test** — mock `useRecordings` to return two rows. Mount page; assert two `RecordingListItem` rendered with titles + status chips, the search field exists, and the FAB (`HRecordFab` placeholder/button) is present and routes to recording mode (emit/route stub).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — `index.vue`: `HAppBar` (light, "Resona"), search `HField` + date filter, list of `RecordingListItem` (wraps `HListRow` + per-item Copy `HIconButton`), terracotta FAB (bottom bar) → `navigateTo` record mode (`/?record=1` or a `recording` state — see Task 14), secondary "Live" action → `/live`. Empty + loading states.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: recordings list page`.

## Task 11: useRecording composable (fetch + status poll)

**Files:** Create `app/composables/useRecording.ts`; Test `test/useRecording.spec.ts`.

- [ ] **Step 1: Failing test** — `useRecording(id)`: fetches the recording (`readItem`) + its transcript (`readItems("transcripts", { filter:{recording:{_eq:id}}, limit:1 })`). When status is `pending`/`transcribing`, `poll()` re-fetches on an interval until `done`/`error`, then stops. Use fake timers; assert it stops polling after `done`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — expose `recording`, `transcript`, `loading`, `error`, `refresh`, `startPolling`/`stopPolling` (clearInterval on done/error and onScopeDispose).
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: useRecording fetch + status poll`.

## Task 12: recording/[id].vue detail page

**Files:** Create `app/pages/recording/[id].vue`, `app/components/hearth/HPlayerDock.vue`; Test `test/recording.page.spec.ts`, `test/hearth/HPlayerDock.spec.ts`.

- [ ] **Step 1: Failing test HPlayerDock** — props `src`, exposes play/pause toggle, progress bar; emits nothing critical — assert play button + progress track render; clicking toggles an `playing` class. (Audio element mocked.)
- [ ] **Step 2:** Implement `HPlayerDock.vue` (52px play FAB, crust-soft track + tomato fill + 16px thumb, mono time row). Use a real `<audio>` ref.
- [ ] **Step 3: Failing test detail page** — mock `useRecording` returning a done recording + transcript. Assert: `HAppBar` with Copy button, transcript text shown, `HPlayerDock` rendered with the asset URL, status chip. For a `pending`/`transcribing` recording, show a "Transkribiert…" state instead of transcript. For an `error` recording, show `error_message`.
- [ ] **Step 4: Failing test delete** — clicking the delete action calls `deleteItem("recordings", id)` (mock) and then `navigateTo("/")`. (Spec §8 requires a delete affordance on the detail page.) Add a confirm step before deleting.
- [ ] **Step 5:** Implement `recording/[id].vue` (header Copy/back + overflow/delete action with confirm → `useDirectus().client.request(deleteItem("recordings", id))`, transcript main area — read-only here, editing arrives in Task 19 — player dock at bottom, `error_message` banner for `error` status). When `audio_file` is null (e.g. some live recordings), hide the player dock.
- [ ] **Step 6: Run** → PASS. **Step 7: Commit** `feat: recording detail page + player dock + delete`.

---

# Phase E — Record & upload (batch write path)

## Task 13: useRecorder composable

**Files:** Create `app/composables/useRecorder.ts`; Test `test/useRecorder.spec.ts`.

- [ ] **Step 1: Failing test** — stub `navigator.mediaDevices.getUserMedia` and global `MediaRecorder` (a fake with `start/pause/resume/stop/state`, firing `ondataavailable`/`onstop`). Assert: `start()` requests mic + sets `recording=true`; `pause()`/`resume()` flip `paused`; `stop()` resolves to a `Blob` and sets `recording=false`; `elapsed` ref increments while recording (fake timers); `levels` ref is an array.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — native `MediaRecorder` capture (`audio/webm`), chunk collection, `start/pause/resume/stop()→Promise<Blob>`, elapsed timer, AnalyserNode RMS → `levels` (guard when `AudioContext` absent in tests), full cleanup (stop tracks, close ctx) on stop/unmount. Accept an optional `stream?: MediaStream` so a caller (the live page, Task 18) can share one mic stream instead of opening a second; when omitted, call `getUserMedia` itself.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: useRecorder (MediaRecorder + levels)`.

## Task 14: HRecordFab + HRecordingSheet (full-screen dark mode)

**Files:** Create `app/components/hearth/HRecordFab.vue`, `HRecordingSheet.vue`; Test `test/hearth/HRecordingSheet.spec.ts`.

- [ ] **Step 1: Failing test** — `HRecordingSheet` props `elapsed`, `levels`, `paused`; emits `pause`, `resume`, `stop`, `cancel`. Assert: mono timer renders `elapsed` formatted `MM:SS`, the right number of waveform bars from `levels`, and clicking stop/cancel/pause emits the events.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — `HRecordFab.vue` (74px terracotta circle, inner ring+dot). `HRecordingSheet.vue` (scrim + dark `#231911` sheet, "AUFNAHME LÄUFT" eyebrow, animated waveform from `levels`, mono timer with pulsing red dot, controls: cancel 54px / stop 78px `#B85032` / pause 54px). Use the exact Hearth values.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: record FAB + recording sheet`.

## Task 15: useUpload + save flow (upload blob, create recording)

**Files:** Create `app/composables/useUpload.ts`, `app/composables/useProfiles.ts`, `app/components/SaveSheet.vue`; wire into `index.vue`; Test `test/useUpload.spec.ts`, `test/useProfiles.spec.ts`, `test/SaveSheet.spec.ts`.

- [ ] **Step 1: Failing test useUpload** — mock `uploadFiles` + `createItem`. `saveRecording({ blob, title, language, profile, transcribe })`: uploads the blob (FormData, file part keyed `file`), then `createItem("recordings", { title, audio_file:<id>, language, profile, source:"batch", status: transcribe ? "pending" : "done", duration_seconds })`. Assert the upload + create payload (incl. the chosen `profile`/`language`, not a hardcoded value).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `useUpload.ts`.
- [ ] **Step 4: Failing test useProfiles** — `useProfiles()` GETs resona-api `GET {resonaApiUrl}/profiles/` (mock `$fetch`) and returns the profile names; on error it falls back to `[runtimeConfig.public.defaultProfile]`. Assert both paths.
- [ ] **Step 5: Implement** `useProfiles.ts`.
- [ ] **Step 6: Failing test SaveSheet** — bottom sheet with: required `title` `HField`; a **language** picker and a **profile** picker (profile options from `useProfiles`, both seeded from the runtimeConfig defaults `defaultLanguage`/`defaultProfile`); Transcribe `HToggle` (on by default — when off, the profile/language pickers are hidden/disabled since no transcription runs); Save/Discard `HButton`s. Save disabled until title non-empty; emits `save({ title, language, profile, transcribe })`/`discard`. Assert the emitted payload reflects the picked profile, not a hardcoded one.
- [ ] **Step 7: Implement** `SaveSheet.vue`; wire FAB → `HRecordingSheet` (useRecorder) → on stop show `SaveSheet` → on save call `useUpload` then refresh list. (This connects Tasks 13/14/15 into `index.vue`.)
- [ ] **Step 8: Run** → PASS. **Step 9: Commit** `feat: record→save→upload batch flow with profile selection`.

## Task 16: Upload existing audio file

**Files:** Modify `app/pages/index.vue` (or a small `UploadButton.vue`); Test `test/upload-file.spec.ts`.

- [ ] **Step 1: Failing test** — a file input accepts an audio file and runs the same `useUpload.saveRecording` path with the file as the blob (title defaults to the filename, editable). Assert `saveRecording` called with the file.
- [ ] **Step 2:** Implement an "Upload" secondary action (hidden `<input type=file accept=audio/*>`), reuse `SaveSheet` for title/profile, then `useUpload`.
- [ ] **Step 3: Run** → PASS. **Step 4: Commit** `feat: upload existing audio file`.

---

# Phase F — Live transcription

## Task 17: useLive composable + PCM util

**Files:** Create `app/utils/pcm.ts`, `app/composables/useLive.ts`; Test `test/pcm.spec.ts`, `test/useLive.spec.ts`.

- [ ] **Step 1: Failing test pcm** — `floatTo16kPCM(float32, inputRate)` downsamples to 16 kHz and returns `Int16Array`; values clamped to [-32768,32767]. Assert length ratio + clamping with a known buffer.
- [ ] **Step 2:** Implement `pcm.ts` (linear-interpolation downsample + int16 encode).
- [ ] **Step 3: Failing test useLive** — mock `@vueuse/core` `useWebSocket` (return controllable `data`/`status`/`send`/`close`). Assert: `start({ language, engine })` opens the WS with the correct `/v1/listen` URL + query params (encoding=linear16, sample_rate=16000, interim_results=true, language, engine, token); `language`/`engine` default to `runtimeConfig.public.defaultLanguage`/`defaultEngine` when not passed. Incoming `{"type":"Results",channel:{alternatives:[{transcript}]},is_final:false}` updates `partial`; `is_final:true` appends to `final` and clears `partial`; `stop()` sends `{"type":"CloseStream"}` then closes. Audio capture stubbed.
- [ ] **Step 4:** Implement `useLive.ts` — `useWebSocket` (autoReconnect), getUserMedia → AudioContext + a `ScriptProcessorNode` (deprecated but simplest for v1; note the option to migrate to an AudioWorklet later) → `floatTo16kPCM` → `send(int16.buffer)`; parse Deepgram frames; expose `partial`, `final`, `fullText` (final+partial), `status`, `start`, `stop`. URL from `resonaApiUrl` + `resonaApiKey` (token query), engine/profile defaults from runtimeConfig. Accept an optional `stream?: MediaStream` (shared with `useRecorder` per Task 18); when omitted, call `getUserMedia` itself.
- [ ] **Step 5: Run** → PASS. **Step 6: Commit** `feat: useLive (WS /v1/listen) + PCM encode`.

## Task 18: live.vue page

**Files:** Create `app/pages/live.vue`; Test `test/live.page.spec.ts`.

- [ ] **Step 1: Failing test** — mock `useLive` + `useUpload`/`useDirectus`. Assert: rolling transcript shows `final`+`partial`; a reconnect banner shows when `status==="CLOSED"` mid-session; on stop, a title prompt appears; confirming uploads the captured audio blob and creates a `recordings` row `source:"live", status:"done"` with the uploaded `audio_file` AND a `transcripts` row with the final text via `createItem("transcripts", …)`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3:** Implement `live.vue` — dark recording-mode styling, rolling transcript, a reconnect banner. **Audio capture:** run a parallel `MediaRecorder` (reuse `useRecorder`) on the same mic stream so a real audio blob exists to persist — acquire `getUserMedia` **once** and pass the single `MediaStream` to both `useLive` and `useRecorder` (add an optional `stream` param to each so they don't each open the mic); on stop, upload it via `useUpload` (without creating the row), then create the `recordings` row (`source:"live", status:"done"`, `audio_file`, `language`, `profile`, `engine` from runtimeConfig defaults) and its `transcripts` row (live path per spec §4). If audio capture is unavailable, save with `audio_file` null. Then `navigateTo("/")`.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: live transcription page`.

---

# Phase G — Edit, export, offline

## Task 19: Transcript editing (optimistic, in detail page)

**Files:** Modify `app/pages/recording/[id].vue`; add `useTranscriptEdit.ts`; Test `test/useTranscriptEdit.spec.ts`.

- [ ] **Step 1: Failing test** — `saveEdit(transcriptId, text)` calls `updateItem("transcripts", id, { text })`; on Directus error it rolls back to the previous text and surfaces an error. Assert optimistic value + rollback.
- [ ] **Step 2:** Implement `useTranscriptEdit.ts` (optimistic ref + rollback).
- [ ] **Step 3:** Make the detail transcript an editable area (debounced autosave or explicit Save) wired to it.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: editable transcript with optimistic save`.

## Task 20: Export (TXT/MD) + copy

**Files:** Create `app/composables/useExport.ts`; wire Copy/Export into detail + list; Test `test/useExport.spec.ts`.

- [ ] **Step 1: Failing test** — `toText(recording, transcript)` returns plain text; `toMarkdown(...)` returns titled MD with metadata; `copy(text)` calls `navigator.clipboard.writeText`; `download(name, content, mime)` creates a blob URL + anchor (stub `URL.createObjectURL` + click). Assert outputs.
- [ ] **Step 2:** Implement `useExport.ts`.
- [ ] **Step 3:** Wire header Copy (detail + per-item list) and Export (TXT/MD) buttons.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: export TXT/MD + copy`.

## Task 21: Offline queue + PWA install

**Files:** Create `app/composables/useOfflineQueue.ts`; expand `pwa` in `nuxt.config.ts`; add `public/` icons; Test `test/useOfflineQueue.spec.ts`.

- [ ] **Step 1: Failing test offline queue** — fake IndexedDB (use `fake-indexeddb` devDep, add it). `enqueue({blob,title,...})` stores a pending item; `pending()` lists them; `sync(uploadFn)` uploads each via the injected `useUpload` and removes succeeded ones, keeping failures. Assert enqueue→sync→drain.
- [ ] **Step 2:** Implement `useOfflineQueue.ts` (IndexedDB via a tiny wrapper or `idb-keyval` devDep). On `navigator.onLine` true / `online` event → `sync`. The list page shows queued items with a local "queued" chip until the Directus row exists.
- [ ] **Step 3:** Expand PWA config in `nuxt.config.ts` (manifest name/short_name/theme_color `#D96846`/background `#FBF5EC`/display standalone/icons incl. maskable; workbox runtimeCaching for `/assets/`). Add icon PNGs to `public/` (192, 512, maskable-512). **Icon source:** the `Resona.html` artifact's SVG logo (terracotta rounded square + white mic glyph, "Resona" wordmark) is the brand mark — extract that SVG and render it to the three PNG sizes (e.g. with `sharp`/`resvg` or any export tool); a plain terracotta square with the mic glyph is an acceptable placeholder if extraction is impractical. `devOptions.enabled` for dev SW test.
- [ ] **Step 4: Run** → PASS (queue test). **Step 5: Manual:** `bun run build && bun run preview`, confirm installable + manifest in devtools.
- [ ] **Step 6: Commit** `feat: offline queue + installable PWA`.

---

# Phase H — End-to-end verification

## Task 22: Full-stack smoke + README + final review

**Files:** Create `README.md`; optional `test/e2e.notes.md`.

- [ ] **Step 1:** `bun run lint` → clean; `bun run typecheck` → clean; `bun run test` → all green.
- [ ] **Step 2: Manual E2E (documented in README)** against a running stack (`docker compose -f docker-compose.resona.yml --profile faster-whisper up` + `directus` + `directus-transcribe`, from the monorepo):
  1. Register/seed a Directus user; log in via the PWA.
  2. Record → save (transcribe on) → row appears `pending` → worker fills it → PWA poll flips it to `done` with transcript.
  3. Upload an existing file → same path.
  4. Live → speak → rolling text → stop → saved as `done`/`source=live` with transcript.
  5. Edit a transcript → persists. Export TXT/MD + Copy work.
  6. Go offline → record → queued → back online → uploads.
  7. Install the PWA (Add to Home Screen) on mobile + desktop.
- [ ] **Step 3:** Write `README.md` (setup, env vars `NUXT_PUBLIC_*`, dev/build/test commands, the E2E checklist, the backend deps it expects).
- [ ] **Step 4: Commit** `docs: README + e2e verification notes`.
- [ ] **Step 5:** Dispatch the final whole-implementation code review; address findings; then run `superpowers:finishing-a-development-branch` for **this PWA repo** (its own git history).

---

## Notes for implementers

- **Backend is not vendored here.** Never import Python or reach into the monorepo. Talk to Directus + resona-api over HTTP only.
- **Auth for live:** browser WS can't set headers — always pass `?token=` for resona-api. Directus SDK handles its own bearer token.
- **CORS:** Directus and resona-api must allow the PWA origin in dev. If blocked, note it (a backend/compose config item, out of scope for this plan).
- **Test env limits:** `AudioContext`, `MediaRecorder`, `IndexedDB`, `navigator.clipboard` are stubbed/polyfilled in tests — guard real usage behind capability checks so SSR/test don't crash.
- **Hearth fidelity:** use the exact values from the spec §9 and the extraction reference; the prop names in Phase B are the agreed Vue API.
