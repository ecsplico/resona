#!/usr/bin/env node
// Checks the `user` policy + owner-guard extension against a bootstrapped Directus
// (CI job `directus` in .github/workflows/pwa.yml; also runnable against a local
// stack). Creates two throwaway users with recordings, asserts who may create
// transcripts and corrections on which recording, then deletes what it created.
//
// Env: DIRECTUS_URL, ADMIN_EMAIL, ADMIN_PASSWORD, DIRECTUS_TOKEN (the worker token
// pinned by bootstrap.mjs). Exit 1 on any failed check.

const URL = (process.env.DIRECTUS_URL || "http://localhost:7700").replace(/\/$/, "");
const { ADMIN_EMAIL, ADMIN_PASSWORD, DIRECTUS_TOKEN } = process.env;
if (!ADMIN_EMAIL || !ADMIN_PASSWORD || !DIRECTUS_TOKEN) {
	console.error("✗ ADMIN_EMAIL, ADMIN_PASSWORD and DIRECTUS_TOKEN must be set");
	process.exit(1);
}

async function call(token, method, path, body) {
	const res = await fetch(`${URL}${path}`, {
		method,
		headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
		...(body ? { body: JSON.stringify(body) } : {}),
	});
	const text = await res.text();
	return { status: res.status, body: text ? JSON.parse(text) : {} };
}

async function data(token, method, path, body) {
	const res = await call(token, method, path, body);
	if (res.status >= 300) throw new Error(`${method} ${path} → ${res.status}: ${JSON.stringify(res.body)}`);
	return res.body.data;
}

const login = async (email, password) => (await data(null, "POST", "/auth/login", { email, password })).access_token;

let failures = 0;
function check(label, res, want) {
	const pass = res.status === want;
	if (!pass) failures++;
	const detail = res.status >= 300 ? ` ${res.body.errors?.[0]?.message ?? ""}` : "";
	console.log(`${pass ? "✓" : "✗"} ${label}: ${res.status}${pass ? "" : ` (want ${want})${detail}`}`);
	return res.body.data;
}

const admin = await login(ADMIN_EMAIL, ADMIN_PASSWORD);
const role = (await data(admin, "GET", "/roles?filter[name][_eq]=user&limit=1"))[0];
if (!role) throw new Error("no `user` role — run bootstrap.mjs first");

const stamp = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const userIds = [];
const recordingIds = [];
async function user(name) {
	const email = `verify-${name}-${stamp}@example.com`;
	userIds.push((await data(admin, "POST", "/users", { email, password: "verify-password-1", role: role.id })).id);
	return login(email, "verify-password-1");
}
async function recording(token, title) {
	const row = await data(token, "POST", "/items/recordings", { title, source: "live", status: "done" });
	recordingIds.push(row.id);
	return row.id;
}

try {
	const a = await user("a");
	const b = await user("b");
	const a1 = await recording(a, "verify a1");
	const a2 = await recording(a, "verify a2");
	const b1 = await recording(b, "verify b1");
	const missing = "00000000-0000-4000-8000-000000000000";

	// transcripts
	const t1 = check("own recording, live save (engine)", await call(a, "POST", "/items/transcripts", { recording: a1, text: "live", engine: "web-speech" }), 200);
	check("own recording, typed (no engine)", await call(a, "POST", "/items/transcripts", { recording: a2, text: "typed" }), 200);
	check("own recording as { id }", await call(a, "POST", "/items/transcripts", { recording: { id: a2 }, text: "obj" }), 200);
	check("someone else's recording", await call(b, "POST", "/items/transcripts", { recording: a1, text: "x" }), 403);
	check("someone else's recording as { id }", await call(b, "POST", "/items/transcripts", { recording: { id: a1 }, text: "x" }), 403);
	check("nonexistent recording", await call(a, "POST", "/items/transcripts", { recording: missing, text: "x" }), 403);
	check("no recording", await call(a, "POST", "/items/transcripts", { text: "x" }), 400);
	check("nested recording create", await call(a, "POST", "/items/transcripts", { recording: { title: "x" }, text: "x" }), 400);
	check("batch [own, someone else's]", await call(a, "POST", "/items/transcripts", [{ recording: a1, text: "x" }, { recording: b1, text: "x" }]), 403);
	check("worker token, any recording", await call(DIRECTUS_TOKEN, "POST", "/items/transcripts", { recording: b1, text: "worker" }), 200);
	check("read own transcript", await call(a, "GET", `/items/transcripts/${t1?.id}`), 200);
	check("read someone else's transcript", await call(b, "GET", `/items/transcripts/${t1?.id}`), 403);
	check("update own transcript", await call(a, "PATCH", `/items/transcripts/${t1?.id}`, { text: "edited" }), 200);

	// corrections
	const corr = { before: "live", after: "edited", edits: [] };
	check("correction on own transcript", await call(a, "POST", "/items/transcript_corrections", { ...corr, transcript: t1?.id, recording: a1 }), 200);
	check("correction: own transcript, other own recording", await call(a, "POST", "/items/transcript_corrections", { ...corr, transcript: t1?.id, recording: a2 }), 403);
	check("correction on someone else's recording", await call(b, "POST", "/items/transcript_corrections", { ...corr, transcript: t1?.id, recording: a1 }), 403);
	check("correction on someone else's transcript via own recording", await call(b, "POST", "/items/transcript_corrections", { ...corr, transcript: t1?.id, recording: b1 }), 403);

	// nothing slipped through
	const stored = await data(admin, "GET", `/items/transcripts?filter[recording][_in]=${a1},${a2},${b1}&fields=text&limit=-1`);
	const leaked = stored.filter(row => row.text === "x");
	check("no rejected transcript stored", { status: leaked.length ? 500 : 200, body: {} }, 200);
}
finally {
	// recordings cascade to their transcripts and corrections
	if (recordingIds.length) await call(admin, "DELETE", "/items/recordings", recordingIds);
	if (userIds.length) await call(admin, "DELETE", "/users", userIds);
}

if (failures) {
	console.error(`✗ ${failures} check(s) failed`);
	process.exit(1);
}
console.log("✓ permissions verified");
