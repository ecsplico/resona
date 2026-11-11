import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { effectScope } from "vue";
import { useWakeLock } from "~/composables/useWakeLock";

// A fake WakeLockSentinel: the browser releases it when the page is hidden.
class FakeSentinel extends EventTarget {
	released = false;
	release = vi.fn(async () => {
		this.drop();
	});

	drop(): void {
		if (this.released) return;
		this.released = true;
		this.dispatchEvent(new Event("release"));
	}
}

const sentinels: FakeSentinel[] = [];
const request = vi.fn(async (_type: string) => {
	const s = new FakeSentinel();
	sentinels.push(s);
	return s;
});

let visibility: DocumentVisibilityState = "visible";

function setVisibility(state: DocumentVisibilityState): void {
	visibility = state;
	document.dispatchEvent(new Event("visibilitychange"));
}

describe("useWakeLock", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		sentinels.length = 0;
		visibility = "visible";
		Object.defineProperty(document, "visibilityState", { get: () => visibility, configurable: true });
		Object.defineProperty(globalThis.navigator, "wakeLock", { value: { request }, configurable: true });
	});

	afterEach(() => {
		Reflect.deleteProperty(globalThis.navigator, "wakeLock");
		Reflect.deleteProperty(document, "visibilityState");
	});

	it("holds a screen lock until release", async () => {
		const lock = useWakeLock();
		lock.acquire();
		await vi.waitFor(() => expect(lock.held.value).toBe(true));
		expect(request).toHaveBeenCalledWith("screen");

		lock.release();
		expect(lock.held.value).toBe(false);
		expect(sentinels[0]!.release).toHaveBeenCalled();
	});

	it("requests the lock again when the page becomes visible after the browser dropped it", async () => {
		const lock = useWakeLock();
		lock.acquire();
		await vi.waitFor(() => expect(lock.held.value).toBe(true));

		// Hidden: the browser releases the lock on its own.
		setVisibility("hidden");
		sentinels[0]!.drop();
		expect(lock.held.value).toBe(false);

		setVisibility("visible");
		await vi.waitFor(() => expect(lock.held.value).toBe(true));
		expect(request).toHaveBeenCalledTimes(2);
		lock.release();
	});

	it("does not request again after release", async () => {
		const lock = useWakeLock();
		lock.acquire();
		await vi.waitFor(() => expect(lock.held.value).toBe(true));
		lock.release();

		setVisibility("hidden");
		setVisibility("visible");
		await Promise.resolve();
		expect(request).toHaveBeenCalledTimes(1);
	});

	it("releases a lock granted after release() was called", async () => {
		let grant!: (s: FakeSentinel) => void;
		request.mockImplementationOnce(() => new Promise<FakeSentinel>((resolve) => {
			grant = resolve;
		}));
		const lock = useWakeLock();
		lock.acquire();
		lock.release();
		const late = new FakeSentinel();
		grant(late);
		await vi.waitFor(() => expect(late.release).toHaveBeenCalled());
		expect(lock.held.value).toBe(false);
	});

	it("is released when its scope is disposed", async () => {
		const scope = effectScope();
		const lock = scope.run(() => useWakeLock())!;
		lock.acquire();
		await vi.waitFor(() => expect(lock.held.value).toBe(true));
		scope.stop();
		expect(sentinels[0]!.release).toHaveBeenCalled();
	});

	it("does nothing when the browser has no wake lock or refuses it", async () => {
		Reflect.deleteProperty(globalThis.navigator, "wakeLock");
		const unsupported = useWakeLock();
		expect(() => unsupported.acquire()).not.toThrow();
		expect(unsupported.held.value).toBe(false);
		unsupported.release();

		Object.defineProperty(globalThis.navigator, "wakeLock", {
			value: { request: vi.fn(async () => {
				throw new DOMException("denied", "NotAllowedError");
			}) },
			configurable: true,
		});
		const refused = useWakeLock();
		refused.acquire();
		await Promise.resolve();
		await Promise.resolve();
		expect(refused.held.value).toBe(false);
		refused.release();
	});
});
