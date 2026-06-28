#!/usr/bin/env bun
// Run the Resona ASR engine NATIVELY on the Apple GPU (MLX / Metal) — driven
// from this repo so the whole stack is operated from resona-pwa.
//
// This is the optimal macOS path: Docker on macOS is CPU-only (no Metal), so the
// fast engine must run on the host. It listens on :7720, which docker-compose.
// full.yml's resona-api probes FIRST (via host.docker.internal) — so once this
// is running, `bun run setup` uses it instead of the CPU docker engine.
//
// It still BUILDS/RUNS from the sibling resona monorepo checkout (it's the engine
// source); point RESONA_REPO at it if it isn't ../resona. First run downloads the
// model into ~/.cache/huggingface (shared with Docker).
//
// Usage:
//   bun run engine                       # mlx-whisper on :7720 (foreground)
//   RESONA_ENGINE=mlx-whisper bun run engine
//   RESONA_REPO=/path/to/resona bun run engine
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const ROOT = dirname(import.meta.dir); // scripts/ → repo root
const REPO = resolve(process.env.RESONA_REPO ?? join(ROOT, "..", "resona"));
const ENGINE = process.env.RESONA_ENGINE ?? "mlx-whisper";
const PORT = process.env.PORT ?? "7720";
const MODEL = process.env.DEFAULT_MLX_WHISPER_MODEL ?? "mlx-community/whisper-large-v3-mlx";

if (!existsSync(REPO)) {
	console.error(`✗ resona monorepo not found at ${REPO} — set RESONA_REPO to its path`);
	process.exit(1);
}

async function run(cmd: string[], env?: Record<string, string>): Promise<number> {
	const proc = Bun.spawn(cmd, {
		cwd: REPO,
		env: { ...process.env, ...env },
		stdio: ["inherit", "inherit", "inherit"],
	});
	await proc.exited;
	return proc.exitCode ?? 0;
}

// Ensure the Apple GPU engines are installed in the workspace venv (idempotent).
console.log("→ uv sync --all-packages --extra apple");
const synced = await run(["uv", "sync", "--all-packages", "--extra", "apple"]);
if (synced !== 0) process.exit(synced);

console.log(`→ ${ENGINE} on :${PORT}  (model: ${MODEL})`);
const code = await run(["uv", "run", `resona-engine-${ENGINE}`], {
	RESONA_ENGINE: ENGINE,
	PORT,
});
process.exit(code);
