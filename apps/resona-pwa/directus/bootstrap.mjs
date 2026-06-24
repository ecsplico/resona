#!/usr/bin/env node
// Idempotent first-start bootstrap for the Resona data layer.
//
// Brings a blank Directus instance up to the state described in
// directus/bootstrap.md: the `recordings` + `transcripts` collections, a
// non-admin `user` role/policy/permissions, and the `transcribe-worker` admin
// user whose static token is pinned to DIRECTUS_TOKEN (so the worker just
// works — no copy-paste). Safe to run repeatedly: every step checks first.
//
// Env (all read from the compose environment / .env):
//   DIRECTUS_URL      base URL          (default http://localhost:7700)
//   ADMIN_EMAIL       admin login       (DIRECTUS_ADMIN_EMAIL)
//   ADMIN_PASSWORD    admin password    (DIRECTUS_ADMIN_PASSWORD)
//   DIRECTUS_TOKEN    worker static token to pin (required, non-empty)

const URL = (process.env.DIRECTUS_URL || "http://localhost:7700").replace(/\/$/, "");
const EMAIL = process.env.ADMIN_EMAIL || process.env.DIRECTUS_ADMIN_EMAIL;
const PASSWORD = process.env.ADMIN_PASSWORD || process.env.DIRECTUS_ADMIN_PASSWORD;
const WORKER_TOKEN = process.env.DIRECTUS_TOKEN;

const WORKER_EMAIL = "transcribe-worker@resona.app";
const OWNER = { _eq: "$CURRENT_USER" }; // owns-row filter

function die(msg) {
	console.error(`✗ ${msg}`);
	process.exit(1);
}

let access = null;
async function api(method, path, body) {
	const res = await fetch(`${URL}${path}`, {
		method,
		headers: {
			"Content-Type": "application/json",
			...(access ? { Authorization: `Bearer ${access}` } : {}),
		},
		...(body ? { body: JSON.stringify(body) } : {}),
	});
	const text = await res.text();
	const json = text ? JSON.parse(text) : {};
	if (!res.ok) {
		const err = json?.errors?.[0]?.message || text || res.statusText;
		throw new Error(`${method} ${path} → ${res.status}: ${err}`);
	}
	return json.data;
}

async function login() {
	const data = await api("POST", "/auth/login", { email: EMAIL, password: PASSWORD });
	access = data.access_token;
}

// ── collections ──────────────────────────────────────────────────────────
const uuidPk = {
	field: "id",
	type: "uuid",
	meta: { hidden: true, readonly: true, interface: "input", special: ["uuid"] },
	schema: { is_primary_key: true, length: 36, has_auto_increment: false },
};

function sysField(field, special) {
	return {
		field,
		type: special.includes("date-created") || special.includes("date-updated") ? "timestamp" : "uuid",
		meta: { special, hidden: true, readonly: true, width: "half" },
		schema: {},
	};
}

const COLLECTIONS = {
	recordings: [
		uuidPk,
		sysField("user_created", ["user-created"]),
		sysField("date_created", ["date-created"]),
		sysField("date_updated", ["date-updated"]),
		{ field: "title", type: "string", schema: {} },
		// audio_file M2O → directus_files; relation added separately
		{ field: "audio_file", type: "uuid", schema: {}, meta: { interface: "file", special: ["file"] } },
		{ field: "duration_seconds", type: "float", schema: {} },
		{ field: "language", type: "string", schema: { default_value: "de" } },
		{ field: "profile", type: "string", schema: { default_value: "default" } },
		{ field: "status", type: "string", schema: { default_value: "pending" } },
		{ field: "error_message", type: "text", schema: {} },
		{ field: "source", type: "string", schema: { default_value: "batch" } },
	],
	transcripts: [
		uuidPk,
		// recording M2O → recordings; relation added separately
		{ field: "recording", type: "uuid", schema: {}, meta: { interface: "select-dropdown-m2o", special: ["m2o"] } },
		{ field: "text", type: "text", schema: {} },
		{ field: "structured", type: "json", schema: {}, meta: { special: ["cast-json"] } },
		{ field: "segments", type: "json", schema: {}, meta: { special: ["cast-json"] } },
		{ field: "engine", type: "string", schema: {} },
		sysField("date_created", ["date-created"]),
		sysField("date_updated", ["date-updated"]),
	],
};

// (collection, field, related_collection) for the M2O foreign keys
const RELATIONS = [
	["recordings", "audio_file", "directus_files"],
	["transcripts", "recording", "recordings"],
];

async function ensureCollections() {
	const existing = new Set((await api("GET", "/collections")).map((c) => c.collection));
	for (const [name, fields] of Object.entries(COLLECTIONS)) {
		if (existing.has(name)) {
			console.log(`= collection ${name} exists`);
			continue;
		}
		await api("POST", "/collections", {
			collection: name,
			meta: { singleton: false },
			schema: {},
			fields,
		});
		console.log(`+ collection ${name}`);
	}
}

async function ensureRelations() {
	const have = new Set((await api("GET", "/relations")).map((r) => `${r.collection}.${r.field}`));
	for (const [collection, field, related] of RELATIONS) {
		if (have.has(`${collection}.${field}`)) {
			console.log(`= relation ${collection}.${field} exists`);
			continue;
		}
		await api("POST", "/relations", {
			collection,
			field,
			related_collection: related,
			schema: { on_delete: related === "recordings" ? "CASCADE" : "SET NULL" },
		});
		console.log(`+ relation ${collection}.${field} → ${related}`);
	}
}

// ── role / policy / permissions ────────────────────────────────────────────
const PERMISSIONS = [
	// recordings: full CRUD scoped to owner
	{ collection: "recordings", action: "read", permissions: { user_created: OWNER } },
	{ collection: "recordings", action: "create", permissions: {} },
	{ collection: "recordings", action: "update", permissions: { user_created: OWNER } },
	{ collection: "recordings", action: "delete", permissions: { user_created: OWNER } },
	// transcripts: scoped to the parent recording's owner
	{ collection: "transcripts", action: "read", permissions: { recording: { user_created: OWNER } } },
	{ collection: "transcripts", action: "create", validation: { recording: { user_created: OWNER } } },
	{ collection: "transcripts", action: "update", permissions: { recording: { user_created: OWNER } } },
	{ collection: "transcripts", action: "delete", permissions: { recording: { user_created: OWNER } } },
	// files: upload + read own (needed for audio upload / playback)
	{ collection: "directus_files", action: "create", permissions: {} },
	{ collection: "directus_files", action: "read", permissions: { uploaded_by: OWNER } },
	{ collection: "directus_files", action: "update", permissions: { uploaded_by: OWNER } },
];

async function ensureUserRole() {
	// role
	let role = (await api("GET", "/roles?filter[name][_eq]=user&limit=1"))[0];
	if (role) console.log(`= role user exists`);
	else {
		role = await api("POST", "/roles", { name: "user", icon: "person", description: "Resona end user" });
		console.log(`+ role user`);
	}

	// policy
	let policy = (await api("GET", "/policies?filter[name][_eq]=user-policy&limit=1"))[0];
	if (policy) console.log(`= policy user-policy exists`);
	else {
		policy = await api("POST", "/policies", {
			name: "user-policy",
			icon: "badge",
			app_access: true,
			admin_access: false,
		});
		console.log(`+ policy user-policy`);
	}

	// access junction: role → policy
	const access_rows = await api(
		"GET",
		`/access?filter[role][_eq]=${role.id}&filter[policy][_eq]=${policy.id}&limit=1`,
	);
	if (access_rows[0]) console.log(`= access role→policy exists`);
	else {
		await api("POST", "/access", { role: role.id, policy: policy.id });
		console.log(`+ access role→policy`);
	}

	// permissions on the policy
	const existing = await api("GET", `/permissions?filter[policy][_eq]=${policy.id}&limit=-1`);
	const seen = new Set(existing.map((p) => `${p.collection}.${p.action}`));
	for (const p of PERMISSIONS) {
		const key = `${p.collection}.${p.action}`;
		if (seen.has(key)) {
			console.log(`= perm ${key} exists`);
			continue;
		}
		await api("POST", "/permissions", {
			policy: policy.id,
			collection: p.collection,
			action: p.action,
			permissions: p.permissions ?? {},
			validation: p.validation ?? {},
			fields: ["*"],
		});
		console.log(`+ perm ${key}`);
	}
}

// ── worker user + pinned static token ──────────────────────────────────────
async function ensureWorker() {
	const adminRole = (await api("GET", "/roles?filter[name][_eq]=Administrator&limit=1"))[0];
	if (!adminRole) die("no Administrator role found");

	let user = (await api("GET", `/users?filter[email][_eq]=${encodeURIComponent(WORKER_EMAIL)}&limit=1`))[0];
	if (!user) {
		user = await api("POST", "/users", {
			email: WORKER_EMAIL,
			first_name: "Transcribe",
			last_name: "Worker",
			role: adminRole.id,
			token: WORKER_TOKEN,
		});
		console.log(`+ worker user (token pinned)`);
	} else {
		await api("PATCH", `/users/${user.id}`, { role: adminRole.id, token: WORKER_TOKEN });
		console.log(`= worker user exists (token re-pinned)`);
	}
}

async function main() {
	if (!EMAIL || !PASSWORD) die("ADMIN_EMAIL / ADMIN_PASSWORD not set");
	if (!WORKER_TOKEN) die("DIRECTUS_TOKEN must be set (non-empty) — it is pinned as the worker's static token");
	console.log(`→ bootstrapping ${URL} as ${EMAIL}`);
	await login();
	await ensureCollections();
	await ensureRelations();
	await ensureUserRole();
	await ensureWorker();
	console.log("✓ bootstrap complete");
}

main().catch((e) => die(e.message));
