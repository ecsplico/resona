import { ref, onScopeDispose } from "vue";
import type { Ref } from "vue";
import type { Recording, Transcript } from "~/types/directus";
import { humanizeError } from "#shared/errors";
import { postprocessRunning } from "~/utils/postprocess";

function isTerminal(status: Recording["status"] | undefined): boolean {
	return status === "done" || status === "error";
}

export function useRecording(id: string) {
	const recording: Ref<Recording | null> = ref(null);
	const transcript: Ref<Transcript | null> = ref(null);
	const loading: Ref<boolean> = ref(false);
	const error: Ref<string | null> = ref(null);

	let timer: ReturnType<typeof setInterval> | null = null;
	let starting = false;
	// Set on scope dispose so an in-flight first refresh never arms the interval.
	let disposed = false;

	async function refresh(): Promise<void> {
		loading.value = true;
		error.value = null;
		try {
			const store = useDataStore();
			// Fetch both in parallel and assign synchronously (no await between the
			// two ref writes). Assigning `recording` first, then awaiting the
			// transcript fetch, lets Vue flush the seeding watcher while `transcript`
			// is still null — for a terminal recording that seeds the editor empty and
			// latches `seeded`, so the real text never lands. See recording/[id].vue.
			const [rec, tx] = await Promise.all([
				store.getRecording(id),
				store.getTranscript(id),
			]);
			recording.value = rec;
			transcript.value = tx;
		}
		catch (err) {
			error.value = humanizeError(err, "Aufnahme konnte nicht geladen werden.");
		}
		finally {
			loading.value = false;
		}
	}

	function stopPolling(): void {
		if (timer !== null) {
			clearInterval(timer);
			timer = null;
		}
	}

	// Nothing left to wait for: transcription terminal and no re-processing
	// ("Nachbearbeiten") queued or running on the transcript.
	function settled(): boolean {
		return isTerminal(recording.value?.status) && !postprocessRunning(transcript.value);
	}

	function startPolling(intervalMs = 4000): void {
		// Guard against multiple concurrent intervals (or a second start while the
		// initial refresh is still in flight).
		if (timer !== null || starting) {
			return;
		}
		starting = true;

		void refresh().then(() => {
			starting = false;
			// Settled on first load (or disposed meanwhile) -> never arm the interval.
			if (disposed || settled()) {
				return;
			}
			timer = setInterval(() => {
				void refresh().then(() => {
					if (settled()) {
						stopPolling();
					}
				});
			}, intervalMs);
		});
	}

	onScopeDispose(() => {
		disposed = true;
		stopPolling();
	});

	return {
		recording,
		transcript,
		loading,
		error,
		refresh,
		startPolling,
		stopPolling,
	};
}
