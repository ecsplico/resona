#!/usr/bin/env bun
// Resona full-stack setup: generate the secrets the stack needs ONCE and persist
// them to .env, then (optionally) bring everything up.
//
// The worker token is the key piece: directus-bootstrap pins DIRECTUS_TOKEN as
// the worker's static token, and the project-worker service reads the SAME
// value to authenticate. Both services read it from .env via compose
// interpolation — so generating it here and writing it to .env "transfers" it to
// the whole stack with no copy-paste.
//
// Idempotent: existing non-placeholder values are left untouched, so re-running
// (or running before every `up`) is safe.
//
// Transcription runs on an external Resona hub (resona monorepo or Coolify);
// this repo starts no engines. Point RESONA_API_URL / RESONA_API_KEY in .env at it.
//
// Usage:
//   bun run setup               # write .env, then up -d --build
//   bun run setup --setup-only  # only write .env (don't start anything)
//   bun run setup --down        # stop the stack (pass extra flags, e.g. --volumes)
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { dirname, join } from "node:path";

const ROOT = dirname(import.meta.dir); // scripts/ → repo root
const ENV_FILE = join(ROOT, ".env");
const EXAMPLE = join(ROOT, ".env.stack.example");
const COMPOSE_BASE = join(ROOT, "docker-compose.yml");

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
const flags = new Set(["--setup-only", "--down"]);
const passthrough = args.filter(a => !flags.has(a)); // forwarded to docker compose

if (args.includes("--setup-only")) {
	console.log("✓ .env ready. Start with: bun run setup");
	process.exit(0);
}

const compose = ["docker", "compose", "-f", COMPOSE_BASE];

if (args.includes("--down")) {
	const cmd = [...compose, "down", ...passthrough];
	console.log(`→ ${cmd.join(" ")}`);
	const proc = Bun.spawn(cmd, { stdio: ["inherit", "inherit", "inherit"] });
	await proc.exited;
	process.exit(proc.exitCode ?? 0);
}

const cmd = [...compose, "up", "-d", "--build", ...passthrough];
console.log(`→ ${cmd.join(" ")}`);
const proc = Bun.spawn(cmd, { stdio: ["inherit", "inherit", "inherit"] });
await proc.exited;
process.exit(proc.exitCode ?? 0);
