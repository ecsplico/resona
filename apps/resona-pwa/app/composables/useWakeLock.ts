import { getCurrentScope, onScopeDispose, ref, type Ref } from "vue";

export interface WakeLockHandle {
	/** A screen wake lock is held right now. */
	held: Ref<boolean>;
	/** Keeps the screen on until `release()`; re-requested whenever the page becomes visible again. */
	acquire(): void;
	/** Lets the screen turn off again. */
	release(): void;
}

function wakeLockApi(): WakeLock | undefined {
	if (typeof navigator === "undefined") return undefined;
	// Typed as always present, but absent on older Safari/Firefox and outside secure contexts.
	const api: WakeLock | undefined = navigator.wakeLock;
	return api;
}

/**
 * Screen wake lock for the lifetime of a recording. The browser drops the lock
 * whenever the page is hidden, so it is requested again on `visibilitychange`
 * back to visible while wanted. Unsupported or refused: nothing happens (the
 * `/debug` probe reports support).
 */
export function useWakeLock(): WakeLockHandle {
	const held = ref(false);
	let wanted = false;
	let sentinel: WakeLockSentinel | null = null;
	let requesting = false;

	async function request(): Promise<void> {
		const api = wakeLockApi();
		if (!wanted || sentinel || requesting || !api?.request) return;
		if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
		requesting = true;
		try {
			const next = await api.request("screen");
			if (!wanted) {
				void next.release().catch(() => {});
				return;
			}
			sentinel = next;
			held.value = true;
			next.addEventListener?.("release", () => {
				if (sentinel !== next) return;
				sentinel = null;
				held.value = false;
			});
		}
		catch {
			// Refused (battery saver, not visible, no user activation): recording goes on without it.
		}
		finally {
			requesting = false;
		}
	}

	function onVisibility(): void {
		if (document.visibilityState === "visible") void request();
	}

	function acquire(): void {
		if (wanted) return;
		wanted = true;
		if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisibility);
		void request();
	}

	function release(): void {
		if (!wanted) return;
		wanted = false;
		if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisibility);
		const current = sentinel;
		sentinel = null;
		held.value = false;
		if (current && !current.released) void current.release().catch(() => {});
	}

	if (getCurrentScope()) onScopeDispose(release);

	return { held, acquire, release };
}
