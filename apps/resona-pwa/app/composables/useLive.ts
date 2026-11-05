import { ref, computed, watch, onScopeDispose } from "vue";
import type { Ref, ComputedRef } from "vue";
// Explicit import (rather than relying on the @vueuse/nuxt auto-import) so the
// `@vueuse/core` module mock binds cleanly in tests.
import { useWebSocket } from "@vueuse/core";
import type { PcmCapture } from "~/composables/usePcmCapture";

export interface LiveStartOptions {
	language?: string;
	engine?: string;
	private?: boolean;
	capture?: PcmCapture;
}

interface DeepgramResults {
	type?: string;
	description?: string;
	channel?: { alternatives?: { transcript?: string }[] };
	is_final?: boolean;
}

function toWsUrl(httpUrl: string): string {
	if (httpUrl.startsWith("https:")) {
		return `wss:${httpUrl.slice("https:".length)}`;
	}
	if (httpUrl.startsWith("http:")) {
		return `ws:${httpUrl.slice("http:".length)}`;
	}
	return httpUrl;
}

export function useLive() {
	const partial: Ref<string> = ref("");
	const final: Ref<string> = ref("");
	const status: Ref<string> = ref("CLOSED");
	const error: Ref<string | null> = ref(null);
	const fullText: ComputedRef<string> = computed(() =>
		partial.value ? `${final.value} ${partial.value}`.trim() : final.value,
	);

	let send: ((data: string | ArrayBufferLike) => void) | null = null;
	let close: (() => void) | null = null;
	let stopDataWatch: (() => void) | null = null;
	let stopStatusWatch: (() => void) | null = null;

	let unsubscribeFrames: (() => void) | null = null;
	const finalListeners = new Set<(text: string) => void>();

	function onFinal(cb: (text: string) => void): () => void {
		finalListeners.add(cb);
		return () => {
			finalListeners.delete(cb);
		};
	}

	function handleMessage(raw: unknown): void {
		if (typeof raw !== "string" || raw.length === 0) {
			return;
		}
		let frame: DeepgramResults;
		try {
			frame = JSON.parse(raw) as DeepgramResults;
		}
		catch {
			return; // ignore non-JSON keepalives etc.
		}
		if (frame.type === "Error") {
			error.value = frame.description || "Live-Transkription fehlgeschlagen";
			stop();
			return;
		}
		if (frame.type !== "Results") {
			return; // ignore Metadata and other frame types
		}
		const transcript = frame.channel?.alternatives?.[0]?.transcript ?? "";
		if (frame.is_final) {
			final.value = final.value ? `${final.value} ${transcript}` : transcript;
			partial.value = "";
			for (const cb of finalListeners) {
				cb(transcript);
			}
		}
		else {
			partial.value = transcript;
		}
	}

	async function start(opts: LiveStartOptions = {}): Promise<void> {
		const cfg = useRuntimeConfig().public;
		const language = opts.language ?? cfg.defaultLanguage;
		const engine = opts.engine ?? "";

		// Concatenate rather than `new URL("/v1/listen", base)`: a root-absolute
		// path would discard a base-path prefix, breaking deployments that serve
		// resona-api under a subpath (e.g. https://host:8443/resona-api).
		const base = toWsUrl(cfg.resonaApiUrl).replace(/\/+$/, "");
		const url = new URL(`${base}/v1/listen`);
		url.searchParams.set("encoding", "linear16");
		url.searchParams.set("sample_rate", "16000");
		url.searchParams.set("interim_results", "true");
		url.searchParams.set("language", language);
		if (engine) {
			url.searchParams.set("engine", engine);
		}
		if (opts.private === false) {
			url.searchParams.set("private", "false");
		}
		url.searchParams.set("token", useHub().token());

		error.value = null;
		final.value = "";
		partial.value = "";

		const sock = useWebSocket(url.toString(), { autoReconnect: { retries: 3, delay: 2000 } });
		send = sock.send as (data: string | ArrayBufferLike) => void;
		close = sock.close;

		stopDataWatch = watch(sock.data, (value) => {
			handleMessage(value);
		});
		stopStatusWatch = watch(
			sock.status,
			(value) => {
				status.value = value;
			},
			{ immediate: true, flush: "sync" },
		);

		unsubscribeFrames = opts.capture?.onFrame((pcm) => {
			send?.(pcm.buffer);
		}) ?? null;
	}

	function stop(): void {
		unsubscribeFrames?.();
		unsubscribeFrames = null;
		if (send) {
			send(JSON.stringify({ type: "CloseStream" }));
		}
		close?.();
		stopDataWatch?.();
		stopStatusWatch?.();
		stopDataWatch = null;
		stopStatusWatch = null;
		send = null;
		close = null;
	}

	onScopeDispose(() => {
		stop();
	});

	return {
		partial,
		final,
		fullText,
		status,
		error,
		start,
		stop,
		onFinal,
	};
}
