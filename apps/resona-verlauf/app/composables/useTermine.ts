import { computed, ref, watch } from "vue";
import type { Ref } from "vue";
import type { Recording } from "~/types/directus";
import { dayBounds, dayOf, groupByEvent, offenRange, openTermine } from "~~/shared/termine";
import type { OffenWindow, TerminAction, TerminEvent, TermineContextView, TermineView } from "~~/shared/termine";

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

/**
 * Call `/api/termine` with the Directus access token. Only dates and event ids go
 * into the query string, never a title.
 */
export async function requestTermine<T>(options: { query?: Record<string, string>; body?: TerminAction }): Promise<T> {
	const { client } = useDirectus();
	const token = (await client.getToken()) ?? "";
	return $fetch<T>(termineUrl(), {
		method: options.body ? "POST" : "GET",
		query: options.query,
		body: options.body,
		headers: token ? { Authorization: `Bearer ${token}` } : {},
	});
}

export function describeTermineError(err: unknown): string {
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
			const [termine, recent] = await Promise.all([
				requestTermine<TermineView>({ query: { date: day } }),
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
			error.value = describeTermineError(err);
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

/**
 * Past EG/FG appointments of the window without a linked dictation and not marked
 * "Kein Diktat". Recordings are joined by event id (`listRecordings({ eventIds })`),
 * so the join does not depend on the 100 newest recordings.
 */
export function useOffeneTermine(span: Readonly<Ref<OffenWindow>>) {
	const store = useDataStore();

	const view = ref<TermineView | null>(null);
	const dictated = ref<ReadonlySet<string>>(new Set());
	const loading = ref(false);
	const error = ref<string | null>(null);
	/** The last appointment marked "Kein Diktat", for "Rückgängig". */
	const lastDeclined = ref<TerminEvent | null>(null);
	const loadedAt = ref(new Date());
	let seq = 0;

	const connected = computed(() => view.value?.connected ?? false);
	const events = computed(() => openTermine(view.value?.events ?? [], dictated.value, loadedAt.value));

	async function refresh(): Promise<void> {
		const mine = ++seq;
		const now = new Date();
		loading.value = true;
		error.value = null;
		try {
			const termine = await requestTermine<TermineView>({ query: offenRange(span.value, dayOf(now)) });
			const linked = await store.listRecordings({ eventIds: termine.events.map(event => event.id) });
			if (mine !== seq) return;
			view.value = termine;
			dictated.value = new Set(linked.map(recording => recording.calendar_event_id ?? ""));
			loadedAt.value = now;
			if (termine.error) error.value = "Google-Kalender konnte nicht gelesen werden.";
		}
		catch (err) {
			if (mine !== seq) return;
			view.value = null;
			error.value = describeTermineError(err);
		}
		finally {
			if (mine === seq) loading.value = false;
		}
	}

	function setDeclined(id: string, declined: boolean): void {
		if (!view.value) return;
		view.value = {
			...view.value,
			events: view.value.events.map((event) => {
				if (event.id !== id) return event;
				const next = { ...event };
				if (declined) next.declined = true;
				else delete next.declined;
				return next;
			}),
		};
	}

	/** Mark `event` "Kein Diktat" (optimistic; reverted when the server refuses). */
	async function decline(event: TerminEvent): Promise<void> {
		setDeclined(event.id, true);
		lastDeclined.value = event;
		error.value = null;
		try {
			await requestTermine({ body: { action: "decline", event: event.id } });
		}
		catch {
			setDeclined(event.id, false);
			if (lastDeclined.value?.id === event.id) lastDeclined.value = null;
			error.value = "„Kein Diktat“ konnte nicht gespeichert werden.";
		}
	}

	/** Undo the last "Kein Diktat". */
	async function undoDecline(): Promise<void> {
		const event = lastDeclined.value;
		if (!event) return;
		lastDeclined.value = null;
		setDeclined(event.id, false);
		error.value = null;
		try {
			await requestTermine({ body: { action: "undecline", event: event.id } });
		}
		catch {
			setDeclined(event.id, true);
			error.value = "Rückgängig machen fehlgeschlagen.";
		}
	}

	watch(span, () => {
		lastDeclined.value = null;
		void refresh();
	});

	return { view, events, connected, loading, error, lastDeclined, refresh, decline, undoDecline };
}

/** One earlier appointment with its dictations (live page context). */
export interface ContextRow {
	event: TerminEvent;
	dictations: Recording[];
}

export interface TerminContext {
	/** Earlier appointments that have dictations, oldest first. */
	before: ContextRow[];
	/** Later appointments, oldest first. */
	after: TerminEvent[];
	error: string | null;
}

// Both halves of the live page's context mount together: one request for both,
// forgotten shortly after so a later visit loads fresh.
const CONTEXT_REUSE_MS = 10_000;
const contextLoads = new Map<string, Promise<TerminContext>>();

async function fetchTerminContext(eventId: string): Promise<TerminContext> {
	try {
		const context = await requestTermine<TermineContextView>({ query: { context: eventId } });
		if (!context.connected) return { before: [], after: [], error: "Google-Kalender ist nicht verbunden." };
		const linked = context.before.length > 0
			? await useDataStore().listRecordings({ eventIds: context.before.map(event => event.id) })
			: [];
		const byEvent = groupByEvent(linked);
		return {
			before: context.before
				.map(event => ({ event, dictations: byEvent.get(event.id) ?? [] }))
				.filter(row => row.dictations.length > 0),
			after: context.after,
			error: context.error ? "Google-Kalender konnte nicht gelesen werden." : null,
		};
	}
	catch (err) {
		return { before: [], after: [], error: describeTermineError(err) };
	}
}

/**
 * The same patient's appointments before and after `eventId` (`/api/termine?context=`),
 * the earlier ones joined with their dictations by event id.
 */
export function loadTerminContext(eventId: string): Promise<TerminContext> {
	let pending = contextLoads.get(eventId);
	if (!pending) {
		pending = fetchTerminContext(eventId);
		contextLoads.set(eventId, pending);
		void pending.finally(() => setTimeout(() => contextLoads.delete(eventId), CONTEXT_REUSE_MS));
	}
	return pending;
}
