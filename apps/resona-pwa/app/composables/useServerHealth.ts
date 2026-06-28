import { computed, ref } from "vue";
import type { ComputedRef, Ref } from "vue";

/**
 * Backend reachability for the app-bar status indicator. Pings the two services
 * the PWA depends on — Directus (recordings/auth) and resona-api (transcription
 * gateway) — on an interval and exposes a reactive aggregate.
 *
 * The ping is a `no-cors` fetch: we only care whether the host answers, not the
 * body (which is opaque and cross-origin). Any response — even a 404 — means the
 * server is up; only a network/DNS failure or timeout counts as down. Combined
 * with `navigator.onLine` so a dropped connection reads as offline immediately.
 *
 * Module singleton so every mount of the indicator shares one poll loop.
 */
export type HealthState = "up" | "partial" | "down" | "unknown";

export interface ServerHealth {
	online: Ref<boolean>;
	directus: Ref<boolean | null>;
	resonaApi: Ref<boolean | null>;
	checking: Ref<boolean>;
	overall: ComputedRef<HealthState>;
	check: () => Promise<void>;
	start: (intervalMs?: number) => void;
	stop: () => void;
}

const PING_TIMEOUT_MS = 4000;
const DEFAULT_INTERVAL_MS = 30_000;

async function ping(url: string): Promise<boolean> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), PING_TIMEOUT_MS);
	try {
		await fetch(url, { mode: "no-cors", cache: "no-store", signal: controller.signal });
		return true;
	}
	catch {
		return false;
	}
	finally {
		clearTimeout(timer);
	}
}

function trimUrl(url: string): string {
	return url.replace(/\/+$/, "");
}

let singleton: ServerHealth | null = null;

function create(): ServerHealth {
	const online = ref(typeof navigator === "undefined" ? true : navigator.onLine);
	const directus: Ref<boolean | null> = ref(null);
	const resonaApi: Ref<boolean | null> = ref(null);
	const checking = ref(false);

	const overall = computed<HealthState>(() => {
		if (!online.value) {
			return "down";
		}
		if (directus.value === null && resonaApi.value === null) {
			return "unknown";
		}
		const up = [directus.value, resonaApi.value].filter(v => v === true).length;
		const known = [directus.value, resonaApi.value].filter(v => v !== null).length;
		if (up === 0) {
			return "down";
		}
		return up === known ? "up" : "partial";
	});

	async function check(): Promise<void> {
		if (typeof navigator !== "undefined") {
			online.value = navigator.onLine;
		}
		if (!online.value) {
			directus.value = false;
			resonaApi.value = false;
			return;
		}
		const cfg = useRuntimeConfig().public;
		checking.value = true;
		try {
			const [d, r] = await Promise.all([
				ping(`${trimUrl(cfg.directusUrl)}/server/health`),
				ping(`${trimUrl(cfg.resonaApiUrl)}/health`),
			]);
			directus.value = d;
			resonaApi.value = r;
		}
		finally {
			checking.value = false;
		}
	}

	let timer: ReturnType<typeof setInterval> | null = null;
	let bound = false;

	function onOnline(): void {
		online.value = true;
		void check();
	}

	function onOffline(): void {
		online.value = false;
		directus.value = false;
		resonaApi.value = false;
	}

	function onVisible(): void {
		if (typeof document !== "undefined" && document.visibilityState === "visible") {
			void check();
		}
	}

	function start(intervalMs = DEFAULT_INTERVAL_MS): void {
		if (timer) {
			return;
		}
		void check();
		timer = setInterval(() => void check(), intervalMs);
		if (typeof window !== "undefined" && !bound) {
			window.addEventListener("online", onOnline);
			window.addEventListener("offline", onOffline);
			document.addEventListener("visibilitychange", onVisible);
			bound = true;
		}
	}

	function stop(): void {
		if (timer) {
			clearInterval(timer);
			timer = null;
		}
		if (typeof window !== "undefined" && bound) {
			window.removeEventListener("online", onOnline);
			window.removeEventListener("offline", onOffline);
			document.removeEventListener("visibilitychange", onVisible);
			bound = false;
		}
	}

	return { online, directus, resonaApi, checking, overall, check, start, stop };
}

export function useServerHealth(): ServerHealth {
	if (!singleton) {
		singleton = create();
	}
	return singleton;
}
