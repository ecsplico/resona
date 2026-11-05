import { runLocalCycle } from "~/data/localProcessor";

const CYCLE_INTERVAL_MS = 5000;

/**
 * Standalone mode: drives the in-browser processor while the app is open —
 * once at start, every 5 s (skipped while the previous cycle still runs) and
 * once when the browser comes back online.
 */
export default defineNuxtPlugin(() => {
	if (useRuntimeConfig().public.dataMode !== "standalone") return;

	let running = false;
	async function tick(): Promise<void> {
		if (running) return;
		running = true;
		try {
			await runLocalCycle();
		}
		catch (err) {
			console.warn("[local-processor] cycle failed", err);
		}
		finally {
			running = false;
		}
	}

	void tick();
	setInterval(() => void tick(), CYCLE_INTERVAL_MS);
	window.addEventListener("online", () => void tick());
});
