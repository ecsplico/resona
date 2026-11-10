import { ref } from "vue";
import type { Ref } from "vue";
import type { Recording } from "~/types/directus";
import { humanizeError } from "#shared/errors";

interface ListOpts {
	search?: string;
	since?: string;
}

export function useRecordings() {
	// Shared state: the list outlives the pane, so returning from a detail page shows it at once.
	const items: Ref<Recording[]> = useState<Recording[]>("recordings-items", () => []);
	const pending: Ref<boolean> = ref(false);
	const error: Ref<string | null> = ref(null);
	let lastOpts: ListOpts = {};
	let latestRequest = 0;

	async function list(opts: ListOpts = {}): Promise<Recording[]> {
		lastOpts = opts;
		const requestId = ++latestRequest;
		pending.value = true;
		error.value = null;
		try {
			const rows = await useDataStore().listRecordings({ search: opts.search, since: opts.since });
			if (requestId !== latestRequest) return items.value;
			items.value = rows;
			return rows;
		}
		catch (err) {
			if (requestId === latestRequest) {
				error.value = humanizeError(err, "Aufnahmen konnten nicht geladen werden.");
			}
			return items.value;
		}
		finally {
			if (requestId === latestRequest) pending.value = false;
		}
	}

	function refresh(): Promise<Recording[]> {
		return list(lastOpts);
	}

	/** Deletes a recording through the data store and drops its row; throws if the store refuses. */
	async function remove(id: string): Promise<void> {
		await useDataStore().deleteRecording(id);
		// A list request still in flight was sent before the delete and would bring the row back.
		latestRequest++;
		pending.value = false;
		items.value = items.value.filter(r => r.id !== id);
	}

	return {
		items,
		pending,
		error,
		list,
		refresh,
		remove,
	};
}
