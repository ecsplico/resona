// Web Share Target handler — imported into the generated Workbox service worker
// via `pwa.workbox.importScripts` in nuxt.config.ts.
//
// The manifest registers a POST share_target at /share-target. The browser
// delivers a shared payload as a POST navigation to that URL. A single-page app
// can't handle a POST navigation on its own (there is no server route in this
// SPA), so we intercept it here in the service worker:
//
//   • audio file present  → stash the blob in the Cache API, then 303-redirect
//     to /share-target?kind=audio. The page reads the blob back and opens the
//     normal SaveSheet → transcribe flow.
//   • text / url only      → 303-redirect to /live?title=<shared text>, reusing
//     the same preset-title flow the calendar entry point uses.
//
// This listener is registered before Workbox's routing (importScripts runs at
// the top of the SW), so it calls event.respondWith() first and wins for the
// share POST. Every other request falls through untouched to Workbox.

const SHARE_PATH = "/share-target";
const SHARE_CACHE = "resona-share-target-v1";
const SHARE_BLOB_KEY = "/share-target/audio";

self.addEventListener("fetch", (event) => {
	const request = event.request;
	if (request.method !== "POST") {
		return;
	}

	let url;
	try {
		url = new URL(request.url);
	}
	catch {
		return;
	}
	if (url.pathname !== SHARE_PATH) {
		return;
	}

	event.respondWith(handleShare(request));
});

async function handleShare(request) {
	try {
		const form = await request.formData();
		const file = form.get("audio");
		const title = String(form.get("title") || "");
		const text = String(form.get("text") || "");
		const link = String(form.get("url") || "");

		if (file && typeof file !== "string" && file.size > 0) {
			const cache = await caches.open(SHARE_CACHE);
			const headers = new Headers();
			headers.set("content-type", file.type || "application/octet-stream");
			headers.set("x-share-filename", encodeURIComponent(file.name || "shared-audio"));
			headers.set("x-share-title", encodeURIComponent(title || text));
			await cache.put(SHARE_BLOB_KEY, new Response(file, { headers }));
			return Response.redirect(`${SHARE_PATH}?kind=audio`, 303);
		}

		// No file — a plain text / link share. Preset the recording title and
		// send the user into the live dictation flow.
		const name = (title || text || link).trim();
		const query = name ? `?title=${encodeURIComponent(name)}` : "";
		return Response.redirect(`/live${query}`, 303);
	}
	catch {
		return Response.redirect(`${SHARE_PATH}?error=1`, 303);
	}
}
