import { onScopeDispose, getCurrentScope } from "vue";
import { floatTo16kPCM } from "~/utils/pcm";

function getAudioContextCtor(): typeof AudioContext | undefined {
	const g = globalThis as Record<string, unknown>;
	return (g.AudioContext ?? g.webkitAudioContext) as typeof AudioContext | undefined;
}

export interface PcmCapture {
	sampleRate: number;
	start(stream?: MediaStream): Promise<void>;
	stop(): void;
	onFrame(cb: (pcm: Int16Array) => void): () => void;
}

/**
 * Microphone → 16 kHz PCM16 frames. One capture can feed several consumers
 * (hub live stream, live correction) via `onFrame`.
 */
export function usePcmCapture(): PcmCapture {
	const sampleRate = 16000;
	const listeners = new Set<(pcm: Int16Array) => void>();

	let audioCtx: AudioContext | null = null;
	let source: MediaStreamAudioSourceNode | null = null;
	let processor: ScriptProcessorNode | null = null;
	let stream: MediaStream | null = null;
	let ownsStream = false;
	let stopped = false;

	function onFrame(cb: (pcm: Int16Array) => void): () => void {
		listeners.add(cb);
		return () => {
			listeners.delete(cb);
		};
	}

	async function start(given?: MediaStream): Promise<void> {
		stopped = false;
		const Ctor = getAudioContextCtor();
		const media = (globalThis as { navigator?: Navigator }).navigator?.mediaDevices;
		if (!Ctor || (!given && !media)) {
			// happy-dom / unsupported browser: skip the capture graph entirely.
			return;
		}

		if (given) {
			stream = given;
			ownsStream = false;
		}
		else {
			stream = await media!.getUserMedia({ audio: true });
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

		const ctx = new Ctor();
		audioCtx = ctx;
		// Chrome's autoplay policy may start the context suspended when created
		// outside a synchronous user-gesture handler (e.g. after an await).
		if (ctx.state === "suspended") {
			await ctx.resume();
		}
		source = ctx.createMediaStreamSource(stream);
		processor = ctx.createScriptProcessor(4096, 1, 1);
		processor.onaudioprocess = (e: AudioProcessingEvent) => {
			if (listeners.size === 0) {
				return;
			}
			const pcm = floatTo16kPCM(e.inputBuffer.getChannelData(0), ctx.sampleRate, sampleRate);
			for (const cb of listeners) {
				cb(pcm);
			}
		};
		source.connect(processor);
		processor.connect(ctx.destination);
	}

	function stop(): void {
		stopped = true;
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

	if (getCurrentScope()) {
		onScopeDispose(stop);
	}

	return { sampleRate, start, stop, onFrame };
}
