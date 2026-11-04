import { ref, computed, watch, onScopeDispose } from "vue";
import type { Ref, ComputedRef } from "vue";
// Explicit import (rather than relying on the @vueuse/nuxt auto-import) so the
// `@vueuse/core` module mock binds cleanly in tests.
import { useWebSocket } from "@vueuse/core";
import { floatTo16kPCM } from "~/utils/pcm";

interface StartOptions {
	language?: string;
	engine?: string;
	stream?: MediaStream;
}

interface DeepgramResults {
	type?: string;
	channel?: { alternatives?: { transcript?: string }[] };
	is_final?: boolean;
}

function getAudioContextCtor(): typeof AudioContext | undefined {
	const g = globalThis as Record<string, unknown>;
	return (g.AudioContext ?? g.webkitAudioContext) as typeof AudioContext | undefined;
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
	const fullText: ComputedRef<string> = computed(() =>
		partial.value ? `${final.value} ${partial.value}`.trim() : final.value,
	);

	let send: ((data: string | ArrayBufferLike) => void) | null = null;
	let close: (() => void) | null = null;
	let stopDataWatch: (() => void) | null = null;
	let stopStatusWatch: (() => void) | null = null;

	let audioCtx: AudioContext | null = null;
	let source: MediaStreamAudioSourceNode | null = null;
	let processor: ScriptProcessorNode | null = null;
	let stream: MediaStream | null = null;
	let ownsStream = false;
	let stopped = false;

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
		if (frame.type !== "Results") {
			return; // ignore Metadata and other frame types
		}
		const transcript = frame.channel?.alternatives?.[0]?.transcript ?? "";
		if (frame.is_final) {
			final.value = final.value ? `${final.value} ${transcript}` : transcript;
			partial.value = "";
		}
		else {
			partial.value = transcript;
		}
	}

	async function startAudio(opts: StartOptions): Promise<void> {
		const Ctor = getAudioContextCtor();
		const media = (globalThis as { navigator?: Navigator }).navigator?.mediaDevices;
		if (!Ctor || !media) {
			// happy-dom / unsupported browser: skip the capture graph entirely.
			return;
		}

		if (opts.stream) {
			stream = opts.stream;
			ownsStream = false;
		}
		else {
			stream = await media.getUserMedia({ audio: true });
			ownsStream = true;
		}

		// stop() may have been called while getUserMedia was pending (quick
		// press-release). Bail out to avoid an orphaned AudioContext/stream.
		if (stopped) {
			if (ownsStream) {
				for (const track of stream.getTracks()) {
					track.stop();
				}
			}
			stream = null;
			ownsStream = false;
			return;
		}

		audioCtx = new Ctor();
		// Chrome's autoplay policy may start the context suspended when created
		// outside a synchronous user-gesture handler (e.g. after an await).
		// resume() is a no-op on already-running contexts.
		if (audioCtx.state === "suspended") {
			await audioCtx.resume();
		}
		source = audioCtx.createMediaStreamSource(stream);
		processor = audioCtx.createScriptProcessor(4096, 1, 1);
		processor.onaudioprocess = (e: AudioProcessingEvent) => {
			if (!send) {
				return;
			}
			const channel = e.inputBuffer.getChannelData(0);
			const pcm = floatTo16kPCM(channel, audioCtx!.sampleRate);
			send(pcm.buffer);
		};
		source.connect(processor);
		processor.connect(audioCtx.destination);
	}

	function teardownAudio(): void {
		try {
			if (processor) {
				processor.onaudioprocess = null;
				processor.disconnect();
			}
			source?.disconnect();
		}
		catch {
			// nodes may already be detached
		}
		if (audioCtx && audioCtx.state !== "closed") {
			void audioCtx.close();
		}
		if (stream && ownsStream) {
			for (const track of stream.getTracks()) {
				track.stop();
			}
		}
		processor = null;
		source = null;
		audioCtx = null;
		stream = null;
		ownsStream = false;
	}

	async function start(opts: StartOptions = {}): Promise<void> {
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
		url.searchParams.set("token", useHub().token());

		stopped = false;
		final.value = "";
		partial.value = "";

		const sock = useWebSocket(url.toString(), { autoReconnect: true });
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

		await startAudio(opts);
	}

	function stop(): void {
		stopped = true;
		teardownAudio();
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
		start,
		stop,
	};
}
