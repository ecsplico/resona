import { Capacitor } from "@capacitor/core";
import { Filesystem } from "@capacitor/filesystem";
import { CapacitorShareTarget, type ShareReceivedEvent } from "@capgo/capacitor-share-target";
import { shareKind, stashShare } from "~/utils/shareStash";

/**
 * Android share sheet → Resona app. The `SEND` intent filters in
 * `android/app/src/main/AndroidManifest.xml` route a shared file to the app;
 * `@capgo/capacitor-share-target` copies it into the app cache and emits
 * `shareReceived` (kept until a listener exists, so a cold start gets it too).
 * Here the file is read into the web view, stashed like a web share
 * (`utils/shareStash.ts`) and `pages/share-target.vue` takes over: token gate,
 * then recording (audio) or document upload → hub job.
 */

/** True inside the Capacitor app, where the share plugin is compiled in. */
export function hasNativeShareTarget(): boolean {
	return Capacitor.isNativePlatform() && Capacitor.isPluginAvailable("CapacitorShareTarget");
}

/**
 * Stashes the first shared file Resona takes and returns the route that handles it:
 * `/share-target?kind=…&n=…` (`n` makes a second share a new navigation), `/live?title=…`
 * for a share without files (as the web share target does), `/share-target?error=1`
 * when no file is usable or reading it failed. Every plugin copy is deleted afterwards.
 */
export async function receiveNativeShare(event: ShareReceivedEvent, nonce: number = Date.now()): Promise<string> {
	const files = event.files ?? [];
	const title = (event.title || event.texts?.[0] || "").trim();
	if (files.length === 0) {
		return title ? `/live?title=${encodeURIComponent(title)}` : "/live";
	}
	try {
		const file = files.find(f => shareKind(f.mimeType, f.name) !== null);
		if (!file) {
			return "/share-target?error=1";
		}
		const kind = shareKind(file.mimeType, file.name)!;
		const res = await fetch(Capacitor.convertFileSrc(file.uri));
		if (!res.ok) {
			return "/share-target?error=1";
		}
		const name = file.name && file.name !== "unknown" ? file.name : "";
		const bytes = await res.arrayBuffer();
		const blob = name ? new File([bytes], name, { type: file.mimeType }) : new Blob([bytes], { type: file.mimeType });
		await stashShare(kind, blob, name, title);
		return `/share-target?kind=${kind}&n=${nonce}`;
	}
	catch {
		return "/share-target?error=1";
	}
	finally {
		for (const f of files) {
			// Plugin copies in the app cache; a content URI it could not copy is not ours to delete.
			if (f.uri.startsWith("/") || f.uri.startsWith("file://")) {
				void Filesystem.deleteFile({ path: f.uri }).catch(() => undefined);
			}
		}
	}
}

/** Hands every share the app receives to `open` (a router push), one after the other. */
export async function listenForNativeShares(open: (url: string) => Promise<unknown>): Promise<void> {
	let queue: Promise<unknown> = Promise.resolve();
	await CapacitorShareTarget.addListener("shareReceived", (event) => {
		queue = queue.then(async () => open(await receiveNativeShare(event))).catch(() => undefined);
	});
}
