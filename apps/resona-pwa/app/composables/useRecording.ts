import { readItem, readItems } from "@directus/sdk";
import { ref, onScopeDispose } from "vue";
import type { Ref } from "vue";
import type { Recording, Transcript } from "~/types/directus";
import { humanizeError } from "#shared/errors";

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

	async function refresh(): Promise<void> {
		loading.value = true;
		error.value = null;
		try {
			const { client } = useDirectus();
			const rec = await client.request(readItem("recordings", id)) as Recording;
			recording.value = rec;

			const rows = await client.request(readItems("transcripts", {
				filter: { recording: { _eq: id } },
				limit: 1,
			})) as Transcript[];
			transcript.value = rows[0] ?? null;
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

	function startPolling(intervalMs = 4000): void {
		// Guard against multiple concurrent intervals (or a second start while the
		// initial refresh is still in flight).
		if (timer !== null || starting) {
			return;
		}
		starting = true;

		void refresh().then(() => {
			starting = false;
			// Already terminal on first load -> never arm the interval.
			if (isTerminal(recording.value?.status)) {
				return;
			}
			timer = setInterval(() => {
				void refresh().then(() => {
					if (isTerminal(recording.value?.status)) {
						stopPolling();
					}
				});
			}, intervalMs);
		});
	}

	onScopeDispose(() => {
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
