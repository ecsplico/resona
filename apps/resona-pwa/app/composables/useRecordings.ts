import { readItems } from "@directus/sdk";
import type { Query } from "@directus/sdk";
import { ref } from "vue";
import type { Ref } from "vue";
import type { Recording, Schema } from "~/types/directus";
import { humanizeError } from "#shared/errors";

interface ListOpts {
	search?: string;
	since?: string;
}

export function useRecordings() {
	const items: Ref<Recording[]> = ref([]);
	const pending: Ref<boolean> = ref(false);
	const error: Ref<string | null> = ref(null);

	async function list(opts: ListOpts = {}): Promise<Recording[]> {
		const filter: Record<string, unknown> = {};
		if (typeof opts.search === "string" && opts.search.length > 0) {
			filter.title = { _icontains: opts.search };
		}
		if (opts.since) {
			filter.date_created = { _gte: opts.since };
		}

		const query: Query<Schema, Recording> = {
			sort: ["-date_created"],
			limit: 100,
		};
		if (Object.keys(filter).length > 0) {
			query.filter = filter as Query<Schema, Recording>["filter"];
		}

		pending.value = true;
		error.value = null;
		try {
			const { client } = useDirectus();
			const rows = await client.request(readItems("recordings", query)) as Recording[];
			items.value = rows;
			return rows;
		}
		catch (err) {
			error.value = humanizeError(err, "Aufnahmen konnten nicht geladen werden.");
			return items.value;
		}
		finally {
			pending.value = false;
		}
	}

	function refresh(): Promise<Recording[]> {
		return list();
	}

	return {
		items,
		pending,
		error,
		list,
		refresh,
	};
}
