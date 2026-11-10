import { ref, onScopeDispose } from "vue";
import type { Ref } from "vue";

/** Container formats the recorder tries, in order; it takes the first the browser supports. */
export const RECORDER_MIME_CANDIDATES = [
	"audio/webm;codecs=opus",
	"audio/webm",
	"audio/ogg;codecs=opus",
	"audio/mp4",
] as const;

function getSupportedMime(): string {
	if (typeof MediaRecorder !== "undefined" && typeof MediaRecorder.isTypeSupported === "function") {
		for (const type of RECORDER_MIME_CANDIDATES) {
			if (MediaRecorder.isTypeSupported(type)) {
				return type;
			}
		}
	}
	return "";
}

function getAudioContextCtor(): typeof AudioContext | undefined {
	const g = globalThis as Record<string, unknown>;
	return (g.AudioContext ?? g.webkitAudioContext) as typeof AudioContext | undefined;
}

export function useRecorder() {
	const recording: Ref<boolean> = ref(false);
	const paused: Ref<boolean> = ref(false);
	const elapsed: Ref<number> = ref(0);
	const levels: Ref<number[]> = ref([]);

	let recorder: MediaRecorder | null = null;
	let recordingMime = "";
	let stream: MediaStream | null = null;
	let ownsStream = false;
	let chunks: Blob[] = [];
	// Bumped by every start() and stop(): a start whose microphone opens late sees it changed.
	let startGen = 0;

	let timer: ReturnType<typeof setInterval> | null = null;

	let audioCtx: AudioContext | null = null;
	let analyser: AnalyserNode | null = null;
	let source: MediaStreamAudioSourceNode | null = null;
	let rafTimer: ReturnType<typeof setInterval> | null = null;

	function startTimer(): void {
		if (timer !== null) {
			return;
		}
		timer = setInterval(() => {
			if (!paused.value) {
				elapsed.value += 1;
			}
		}, 1000);
	}

	function stopTimer(): void {
		if (timer !== null) {
			clearInterval(timer);
			timer = null;
		}
	}

	function startLevels(src: MediaStream): void {
		const Ctor = getAudioContextCtor();
		if (!Ctor) {
			// happy-dom / unsupported browser: leave levels empty.
			return;
		}
		try {
			audioCtx = new Ctor();
			analyser = audioCtx.createAnalyser();
			analyser.fftSize = 256;
			source = audioCtx.createMediaStreamSource(src);
			source.connect(analyser);

			const buf = new Uint8Array(analyser.fftSize);
			rafTimer = setInterval(() => {
				if (!analyser || paused.value) {
					return;
				}
				analyser.getByteTimeDomainData(buf);
				let sum = 0;
				for (let i = 0; i < buf.length; i += 1) {
					const v = (buf[i]! - 128) / 128;
					sum += v * v;
				}
				const rms = Math.sqrt(sum / buf.length);
				const next = [...levels.value, rms];
				levels.value = next.slice(-100);
			}, 100);
		}
		catch {
			// Audio analysis is best-effort; never let it break recording.
			teardownLevels();
		}
	}

	function teardownLevels(): void {
		if (rafTimer !== null) {
			clearInterval(rafTimer);
			rafTimer = null;
		}
		try {
			source?.disconnect();
			analyser?.disconnect();
		}
		catch {
			// nodes may already be detached
		}
		if (audioCtx && audioCtx.state !== "closed") {
			void audioCtx.close();
		}
		source = null;
		analyser = null;
		audioCtx = null;
	}

	function stopTracks(): void {
		if (stream && ownsStream) {
			for (const track of stream.getTracks()) {
				track.stop();
			}
		}
		stream = null;
		ownsStream = false;
	}

	function cleanup(): void {
		stopTimer();
		teardownLevels();
		stopTracks();
		recorder = null;
	}

	/**
	 * Starts recording; false when it did not start: already recording, or a
	 * `stop()` or newer `start()` came while the microphone was still opening
	 * (that late stream's tracks are stopped, nothing else is touched).
	 */
	async function start(existing?: MediaStream): Promise<boolean> {
		if (recording.value) {
			return false;
		}
		startGen += 1;
		const gen = startGen;

		let next: MediaStream;
		let owned: boolean;
		if (existing) {
			next = existing;
			owned = false;
		}
		else {
			// Browsers hide mediaDevices outside a secure context (HTTPS or localhost),
			// e.g. on http://<hostname>:7730 from another device.
			// lib.dom types mediaDevices as always present; at runtime it can be undefined.
			const media: MediaDevices | undefined = navigator.mediaDevices;
			if (!media?.getUserMedia) {
				throw new Error("Mikrofon nicht verfügbar – die App muss über HTTPS oder localhost geöffnet sein.");
			}
			try {
				next = await media.getUserMedia({ audio: true });
			}
			catch (err) {
				throw new Error(
					err instanceof DOMException && err.name === "NotAllowedError"
						? "Mikrofonzugriff wurde verweigert."
						: "Mikrofon konnte nicht gestartet werden.",
					{ cause: err },
				);
			}
			owned = true;
			if (gen !== startGen || recording.value) {
				// Superseded while the microphone opened: release this stream only.
				for (const track of next.getTracks()) {
					track.stop();
				}
				return false;
			}
		}
		// A previous stream (should not exist here) is released, never leaked.
		stopTracks();
		stream = next;
		ownsStream = owned;

		chunks = [];
		recordingMime = getSupportedMime();
		recorder = new MediaRecorder(next, recordingMime ? { mimeType: recordingMime } : undefined);
		recorder.ondataavailable = (e: BlobEvent) => {
			if (e.data && e.data.size > 0) {
				chunks.push(e.data);
			}
		};
		recorder.start();

		elapsed.value = 0;
		levels.value = [];
		recording.value = true;
		paused.value = false;

		startTimer();
		startLevels(next);
		return true;
	}

	function pause(): void {
		if (!recording.value || paused.value) {
			return;
		}
		recorder?.pause();
		paused.value = true;
	}

	function resume(): void {
		if (!recording.value || !paused.value) {
			return;
		}
		recorder?.resume();
		paused.value = false;
	}

	function stop(): Promise<Blob> {
		// A start still waiting for the microphone is now stale.
		startGen += 1;
		return new Promise<Blob>((resolve) => {
			if (!recorder) {
				resolve(new Blob([], { type: recordingMime }));
				return;
			}
			const rec = recorder;
			const mime = recordingMime;
			rec.onstop = () => {
				const blob = new Blob(chunks, { type: mime });
				recording.value = false;
				paused.value = false;
				cleanup();
				resolve(blob);
			};
			rec.stop();
		});
	}

	onScopeDispose(() => {
		startGen += 1;
		cleanup();
	});

	return {
		recording,
		paused,
		elapsed,
		levels,
		start,
		pause,
		resume,
		stop,
	};
}
