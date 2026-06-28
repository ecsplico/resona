#!/usr/bin/env bun
// Resona full-stack setup: generate the secrets the stack needs ONCE and persist
// them to .env, then (optionally) bring everything up.
//
// The worker token is the key piece: directus-bootstrap pins DIRECTUS_TOKEN as
// the transcribe-worker's static token, and the directus-transcribe worker reads
// the SAME value to authenticate. Both services read it from .env via compose
// interpolation — so generating it here and writing it to .env "transfers" it to
// the whole stack with no copy-paste.
//
// Idempotent: existing non-placeholder values are left untouched, so re-running
// (or running before every `up`) is safe.
//
// Engine selection: a native MLX engine (Apple GPU, `bun run engine` on :7720)
// is PREFERRED over the dockerized CPU faster-whisper engine. resona-api always
// probes the native host engine first; this script additionally auto-detects
// whether one is live and only starts the CPU fallback container (compose
// profile `cpu-engine`) when it is NOT — so a running native MLX is used instead
// of spinning up the CPU docker engine.
//
// Usage:
//   bun run setup               # write .env, auto-pick engine, then up -d --build
//   bun run setup --setup-only  # only write .env (don't start anything)
//   bun run setup --cpu         # force-start the CPU docker engine (ignore native)
//   bun run setup --no-cpu      # never start the CPU docker engine (native only)
//   bun run setup --down        # stop the stack (pass extra flags, e.g. --volumes)
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { dirname, join } from "node:path";

const ROOT = dirname(import.meta.dir); // scripts/ → repo root
const ENV_FILE = join(ROOT, ".env");
const EXAMPLE = join(ROOT, ".env.stack.example");
const COMPOSE_FILE = join(ROOT, "docker-compose.full.yml");

const PLACEHOLDERS = new Set(["", "change-me", "change-me-in-env"]);

if (!existsSync(ENV_FILE)) {
	copyFileSync(EXAMPLE, ENV_FILE);
	console.log("→ created .env from .env.stack.example");
}

// Set KEY in .env to a fresh random hex value, but ONLY if it's currently empty
// or a known placeholder. Adds the line if missing.
function genSecret(key: string, bytes: number): void {
	let env = readFileSync(ENV_FILE, "utf8");
	const line = new RegExp(`^${key}=(.*)$`, "m");
	const match = env.match(line);
	const current = match?.[1] ?? "";

	if (match && !PLACEHOLDERS.has(current)) {
		console.log(`= ${key} already set (left unchanged)`);
		return;
	}

	const value = randomBytes(bytes).toString("hex");
	env = match
		? env.replace(line, `${key}=${value}`)
		: `${env}${env.endsWith("\n") ? "" : "\n"}${key}=${value}\n`;
	writeFileSync(ENV_FILE, env);
	console.log(`→ generated ${key}`);
}

genSecret("DIRECTUS_TOKEN", 24); // worker static token (pinned by bootstrap)
genSecret("DIRECTUS_SECRET", 32); // Directus instance secret (JWT signing)

const args = process.argv.slice(2);
const flags = new Set(["--setup-only", "--cpu", "--no-cpu", "--down"]);
const passthrough = args.filter(a => !flags.has(a)); // forwarded to docker compose

if (args.includes("--setup-only")) {
	console.log("✓ .env ready. Start with: bun run setup");
	process.exit(0);
}

// Is a native MLX engine answering on the host (the preferred engine)?
async function nativeEngineUp(): Promise<boolean> {
	try {
		const res = await fetch("http://localhost:7720/health", {
			signal: AbortSignal.timeout(1500),
		});
		return res.ok;
	}
	catch {
		return false;
	}
}

const compose = ["docker", "compose", "-f", COMPOSE_FILE];

if (args.includes("--down")) {
	// Include the profile so a CPU engine started earlier is torn down too.
	const cmd = [...compose, "--profile", "cpu-engine", "down", ...passthrough];
	console.log(`→ ${cmd.join(" ")}`);
	const proc = Bun.spawn(cmd, { stdio: ["inherit", "inherit", "inherit"] });
	await proc.exited;
	process.exit(proc.exitCode ?? 0);
}

// Decide whether to start the CPU docker engine: explicit flags win, else
// start it only when no native MLX engine is reachable.
let useCpu: boolean;
if (args.includes("--cpu")) useCpu = true;
else if (args.includes("--no-cpu")) useCpu = false;
else useCpu = !(await nativeEngineUp());

console.log(
	useCpu
		? "→ no native MLX engine on :7720 → starting the CPU docker engine (profile cpu-engine)"
		: "→ native MLX engine detected on :7720 → using it (CPU docker engine stays off)",
);

const profile = useCpu ? ["--profile", "cpu-engine"] : [];
const cmd = [...compose, ...profile, "up", "-d", "--build", ...passthrough];
console.log(`→ ${cmd.join(" ")}`);
const proc = Bun.spawn(cmd, { stdio: ["inherit", "inherit", "inherit"] });
await proc.exited;
process.exit(proc.exitCode ?? 0);
