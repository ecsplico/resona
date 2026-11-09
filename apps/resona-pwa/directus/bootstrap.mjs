#!/usr/bin/env node
// Idempotent first-start bootstrap for the Resona data layer.
//
// Brings a blank Directus instance up to the state described in
// directus/bootstrap.md: the `recordings` + `transcripts` + `documents`
// collections (missing fields are added to existing installs), a non-admin
// `user` role/policy/permissions, and the `transcribe-worker` admin
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

// Plain data field. Every field carries `meta` so it gets a directus_fields row:
// `directus schema snapshot` only exports fields that have one.
const INTERFACE = { string: "input", text: "input-multiline", integer: "input", float: "input" };
function plain(field, type, schema = {}) {
	return { field, type, schema, meta: { interface: INTERFACE[type] } };
}

const COLLECTIONS = {
	recordings: [
		uuidPk,
		sysField("user_created", ["user-created"]),
		sysField("date_created", ["date-created"]),
		sysField("date_updated", ["date-updated"]),
		plain("title", "string"),
		// audio_file M2O → directus_files; relation added separately
		{ field: "audio_file", type: "uuid", schema: {}, meta: { interface: "file", special: ["file"] } },
		plain("duration_seconds", "float"),
		plain("language", "string", { default_value: "de" }),
		plain("profile", "string", { default_value: "default" }),
		plain("status", "string", { default_value: "pending" }),
		plain("error_message", "text"),
		plain("source", "string", { default_value: "batch" }),
		plain("review_status", "string", { default_value: "editing" }),
		// durable Resona hub job (project-worker); null when no job is in flight
		plain("hub_job_id", "integer"),
		// hub Idempotency-Key of the current submission attempt; kept across stale
		// recovery so a lost create response never yields a second hub job
		plain("hub_job_key", "string"),
	],
	transcripts: [
		uuidPk,
		// recording M2O → recordings; relation added separately
		{ field: "recording", type: "uuid", schema: {}, meta: { interface: "select-dropdown-m2o", special: ["m2o"] } },
		plain("text", "text"),
		{ field: "structured", type: "json", schema: {}, meta: { special: ["cast-json"] } },
		{ field: "segments", type: "json", schema: {}, meta: { special: ["cast-json"] } },
		plain("engine", "string"),
		// hub JobResult.provenance: worker, model, seconds per stage (expert mode)
		{ field: "provenance", type: "json", schema: {}, meta: { special: ["cast-json"] } },
		// Re-processing with a hub postprocess profile ("Nachbearbeiten"): `raw_text`
		// keeps the transcript the profile runs on (null = `text` is raw), `applied_profile`
		// names the profile whose output `text` is. The postprocess_* fields carry the
		// hub text job (project-worker / standalone processor), like hub_job_* on recordings.
		plain("raw_text", "text"),
		plain("applied_profile", "string"),
		// the last profile run's output as delivered: the PWA asks before a re-run or
		// restore would drop edits made to `text` since
		plain("processed_text", "text"),
		plain("postprocess_status", "string"),
		plain("postprocess_profile", "string"),
		plain("postprocess_error", "text"),
		plain("postprocess_queue_reason", "string"),
		plain("postprocess_job_id", "integer"),
		plain("postprocess_job_key", "string"),
		sysField("date_created", ["date-created"]),
		sysField("date_updated", ["date-updated"]),
	],
	// A user's edit of an ASR transcript, kept as correction data for engine tuning.
	transcript_corrections: [
		uuidPk,
		{ field: "transcript", type: "uuid", schema: {}, meta: { interface: "select-dropdown-m2o", special: ["m2o"] } },
		{ field: "recording", type: "uuid", schema: {}, meta: { interface: "select-dropdown-m2o", special: ["m2o"] } },
		sysField("user_created", ["user-created"]),
		sysField("date_created", ["date-created"]),
		plain("before", "text"),
		plain("after", "text"),
		{ field: "edits", type: "json", schema: {}, meta: { special: ["cast-json"] } },
		plain("engine", "string"),
		plain("language", "string"),
	],
	documents: [
		uuidPk,
		sysField("user_created", ["user-created"]),
		sysField("date_created", ["date-created"]),
		sysField("date_updated", ["date-updated"]),
		plain("title", "string"),
		// file M2O → directus_files; relation added separately
		{ field: "file", type: "uuid", schema: {}, meta: { interface: "file", special: ["file"] } },
		plain("status", "string", { default_value: "pending" }),
		plain("error_message", "text"),
		plain("markdown", "text"),
		{ field: "pages", type: "json", schema: {}, meta: { special: ["cast-json"] } },
		{ field: "provenance", type: "json", schema: {}, meta: { special: ["cast-json"] } },
		plain("hub_job_id", "integer"),
		// hub Idempotency-Key of the current submission attempt; kept across stale
		// recovery so a lost create response never yields a second hub job
		plain("hub_job_key", "string"),
	],
};

// Custom fields on Directus system collections (added next to the app collections).
// `hub_token`: the user's own Resona hub token, so a second device of the same user
// does not ask again. Hidden in the Data Studio; the `user` policy below lets only the
// user themself read/update it (filter id = $CURRENT_USER), never others'.
const SYSTEM_FIELDS = {
	directus_users: [
		{
			field: "hub_token",
			type: "string",
			schema: {},
			meta: { interface: "input", hidden: true, note: "Resona hub token of this user (synced across their devices)" },
		},
	],
};

// (collection, field, related_collection) for the M2O foreign keys
const RELATIONS = [
	["recordings", "audio_file", "directus_files"],
	["transcripts", "recording", "recordings"],
	["transcript_corrections", "transcript", "transcripts"],
	["transcript_corrections", "recording", "recordings"],
	["documents", "file", "directus_files"],
];

async function ensureCollections() {
	const existing = new Set((await api("GET", "/collections")).map(c => c.collection));
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

// ensureCollections skips existing collections, so fields added to COLLECTIONS
// later (e.g. recordings.hub_job_id) are created here on existing installs.
// Fields created earlier without `meta` get it backfilled, so the schema
// snapshot includes them.
async function ensureFields() {
	for (const [name, fields] of [...Object.entries(COLLECTIONS), ...Object.entries(SYSTEM_FIELDS)]) {
		const have = new Map((await api("GET", `/fields/${name}`)).map(f => [f.field, f]));
		for (const field of fields) {
			const current = have.get(field.field);
			if (!current) {
				await api("POST", `/fields/${name}`, field);
				console.log(`+ field ${name}.${field.field}`);
			}
			else if (!current.meta && field.meta) {
				await api("PATCH", `/fields/${name}/${field.field}`, { meta: field.meta });
				console.log(`~ meta ${name}.${field.field}`);
			}
		}
	}
}

async function ensureRelations() {
	const have = new Set((await api("GET", "/relations")).map(r => `${r.collection}.${r.field}`));
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
// Writes carry explicit field lists: the job bookkeeping (`hub_job_*`,
// `postprocess_job_*`, `postprocess_error`, `postprocess_queue_reason`) belongs to the
// project-worker, so a user (or a stale tab) can never point a row at another hub job.
const PERMISSIONS = [
	// recordings: full CRUD scoped to owner
	{ collection: "recordings", action: "read", permissions: { user_created: OWNER } },
	{
		collection: "recordings", action: "create", permissions: {},
		fields: ["title", "audio_file", "language", "profile", "source", "status", "duration_seconds"],
	},
	{
		collection: "recordings", action: "update", permissions: { user_created: OWNER },
		fields: ["title", "status", "error_message", "review_status", "audio_file", "duration_seconds"],
	},
	{ collection: "recordings", action: "delete", permissions: { user_created: OWNER } },
	// transcripts: scoped to the parent recording's owner
	{ collection: "transcripts", action: "read", permissions: { recording: { user_created: OWNER } } },
	{
		collection: "transcripts", action: "create", validation: { recording: { user_created: OWNER } },
		fields: ["recording", "text", "engine"],
	},
	{
		collection: "transcripts", action: "update", permissions: { recording: { user_created: OWNER } },
		// the text plus the "Nachbearbeiten" request (status `pending`) and undo (null);
		// `processing`/`done`/`error` are the project-worker's
		validation: { _or: [{ postprocess_status: { _null: true } }, { postprocess_status: { _eq: "pending" } }] },
		fields: ["text", "raw_text", "applied_profile", "processed_text", "postprocess_status", "postprocess_profile"],
	},
	{ collection: "transcripts", action: "delete", permissions: { recording: { user_created: OWNER } } },
	// corrections: write + read own; never updated or deleted by users
	{ collection: "transcript_corrections", action: "read", permissions: { user_created: OWNER } },
	{ collection: "transcript_corrections", action: "create", validation: { recording: { user_created: OWNER } } },
	// documents: full CRUD scoped to owner
	{ collection: "documents", action: "read", permissions: { user_created: OWNER } },
	{ collection: "documents", action: "create", permissions: {}, fields: ["title", "file", "status"] },
	{ collection: "documents", action: "update", permissions: { user_created: OWNER }, fields: ["status", "error_message"] },
	{ collection: "documents", action: "delete", permissions: { user_created: OWNER } },
	// files: upload + read own (needed for audio/document upload + playback)
	{ collection: "directus_files", action: "create", permissions: {} },
	{ collection: "directus_files", action: "read", permissions: { uploaded_by: OWNER } },
	{ collection: "directus_files", action: "update", permissions: { uploaded_by: OWNER } },
	// users: the own hub token only (never anyone else's row, no other fields)
	{ collection: "directus_users", action: "read", permissions: { id: OWNER }, fields: ["id", "hub_token"] },
	{ collection: "directus_users", action: "update", permissions: { id: OWNER }, fields: ["hub_token"] },
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

	// permissions on the policy: created when missing, brought in line with
	// PERMISSIONS when an older bootstrap left them different (e.g. `fields: ["*"]`)
	const existing = await api("GET", `/permissions?filter[policy][_eq]=${policy.id}&limit=-1`);
	const byKey = new Map(existing.map(p => [`${p.collection}.${p.action}`, p]));
	for (const p of PERMISSIONS) {
		const key = `${p.collection}.${p.action}`;
		const wanted = {
			permissions: p.permissions ?? {},
			validation: p.validation ?? {},
			fields: p.fields ?? ["*"],
		};
		const have = byKey.get(key);
		if (!have) {
			await api("POST", "/permissions", { policy: policy.id, collection: p.collection, action: p.action, ...wanted });
			console.log(`+ perm ${key}`);
			continue;
		}
		const current = {
			permissions: have.permissions ?? {},
			validation: have.validation ?? {},
			fields: have.fields ?? [],
		};
		if (stableJson(current) === stableJson(wanted)) {
			console.log(`= perm ${key} exists`);
			continue;
		}
		await api("PATCH", `/permissions/${have.id}`, wanted);
		console.log(`~ perm ${key} updated`);
	}
}

/** JSON with object keys sorted, so equal rules compare equal whatever their key order. */
function stableJson(value) {
	return JSON.stringify(value, (_, v) =>
		v && typeof v === "object" && !Array.isArray(v)
			? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b)))
			: v);
}

// ── worker user + pinned static token ──────────────────────────────────────
async function ensureWorker() {
	const adminRole = (await api("GET", "/roles?filter[name][_eq]=Administrator&limit=1"))[0];
	if (!adminRole) die("no Administrator role found");

	const user = (await api("GET", `/users?filter[email][_eq]=${encodeURIComponent(WORKER_EMAIL)}&limit=1`))[0];
	if (!user) {
		await api("POST", "/users", {
			email: WORKER_EMAIL,
			first_name: "Transcribe",
			last_name: "Worker",
			role: adminRole.id,
			token: WORKER_TOKEN,
		});
		console.log(`+ worker user (token pinned)`);
	}
	else {
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
	await ensureFields();
	await ensureRelations();
	await ensureUserRole();
	await ensureWorker();
	console.log("✓ bootstrap complete");
}

main().catch(e => die(e.message));
