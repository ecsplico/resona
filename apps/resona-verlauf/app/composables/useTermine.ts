import { computed, ref, watch } from "vue";
import type { Ref } from "vue";
import type { Recording } from "~/types/directus";
import { dayBounds, groupByEvent } from "~~/shared/termine";
import type { TerminEvent, TermineView } from "~~/shared/termine";

/** One appointment of the day with the dictations linked to it. */
export interface TerminRow {
	event: TerminEvent;
	dictations: Recording[];
}

/** `/api/termine` on this origin, or on `verlaufApiBase` (Capacitor app shell). */
function termineUrl(): string {
	const base = String(useRuntimeConfig().public.verlaufApiBase ?? "").replace(/\/$/, "");
	return `${base}/api/termine`;
}

function describeError(err: unknown): string {
	const status = err && typeof err === "object" && "statusCode" in err ? err.statusCode : undefined;
	if (status === 401) return "Anmeldung abgelaufen. Bitte neu anmelden.";
	if (status === 403) return "Dieses Konto ist für den Kalender nicht freigegeben.";
	if (status === 501) return "Der Kalender ist auf dem Server nicht eingerichtet.";
	return "Termine konnten nicht geladen werden.";
}

/**
 * The day's EG/FG appointments (server route `/api/termine`, authorised with the
 * Directus access token) joined with the user's recordings by `calendar_event_id`.
 */
export function useTermine(date: Readonly<Ref<string>>) {
	const { client } = useDirectus();
	const store = useDataStore();

	const view = ref<TermineView | null>(null);
	const recordings = ref<Recording[]>([]);
	const loading = ref(false);
	const error = ref<string | null>(null);
	let seq = 0;

	const connected = computed(() => view.value?.connected ?? false);

	const rows = computed<TerminRow[]>(() => {
		const byEvent = groupByEvent(recordings.value);
		return (view.value?.events ?? []).map(event => ({ event, dictations: byEvent.get(event.id) ?? [] }));
	});

	async function refresh(): Promise<void> {
		const mine = ++seq;
		const day = date.value;
		loading.value = true;
		error.value = null;
		try {
			const token = (await client.getToken()) ?? "";
			const [termine, recent] = await Promise.all([
				$fetch<TermineView>(termineUrl(), {
					query: { date: day },
					headers: token ? { Authorization: `Bearer ${token}` } : {},
				}),
				// Dictations on an appointment are made that day or later.
				store.listRecordings({ since: dayBounds(day).timeMin }),
			]);
			if (mine !== seq) return;
			view.value = termine;
			recordings.value = recent;
			if (termine.error) error.value = "Google-Kalender konnte nicht gelesen werden.";
		}
		catch (err) {
			if (mine !== seq) return;
			view.value = null;
			error.value = describeError(err);
		}
		finally {
			if (mine === seq) loading.value = false;
		}
	}

	/** Browser navigation target for the one-time Google consent (token as query). */
	async function connectUrl(): Promise<string> {
		const token = (await client.getToken()) ?? "";
		const params = new URLSearchParams({ action: "connect", access_token: token });
		return `${termineUrl()}?${params.toString()}`;
	}

	watch(date, () => void refresh());

	return { view, rows, connected, loading, error, refresh, connectUrl };
}
