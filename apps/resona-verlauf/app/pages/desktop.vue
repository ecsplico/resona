<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import type { Recording } from "~/types/directus";
import { formatDateTime } from "~~/shared/termine";

// Desk view: copy each finished dictation into the patient record, then tick
// "Übernommen" (recordings.adopted_at). Nothing is marked automatically.
const PREVIEW_CHARS = 200;
const TRANSCRIPT_CONCURRENCY = 6;

const store = useDataStore();

const recordings = ref<Recording[]>([]);
/** Transcript text per recording id; `null` = no transcript (yet). */
const texts = ref<Record<string, string | null>>({});
const loading = ref(false);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const onlyOpen = ref(true);
const expanded = ref<Record<string, boolean>>({});
const copiedId = ref<string | null>(null);
const savingId = ref<string | null>(null);
// Ticked in this visit: stays in the "nur offene" list until the next reload,
// so a row does not vanish under the pointer right after the tick.
const adoptedNow = ref<Record<string, boolean>>({});
let loadSeq = 0;
let copiedTimer: ReturnType<typeof setTimeout> | undefined;

const visible = computed(() => recordings.value.filter(r => !onlyOpen.value || !r.adopted_at || adoptedNow.value[r.id]));

async function loadTranscripts(rows: Recording[], seq: number): Promise<void> {
	const queue = rows.filter(r => r.status === "done");
	async function worker(): Promise<void> {
		for (let next = queue.shift(); next; next = queue.shift()) {
			const id = next.id;
			try {
				const transcript = await store.getTranscript(id);
				if (seq === loadSeq) texts.value = { ...texts.value, [id]: transcript?.text ?? null };
			}
			catch {
				if (seq === loadSeq) texts.value = { ...texts.value, [id]: null };
			}
		}
	}
	await Promise.all(Array.from({ length: TRANSCRIPT_CONCURRENCY }, worker));
}

async function load(): Promise<void> {
	const seq = ++loadSeq;
	loading.value = true;
	error.value = null;
	try {
		const rows = await store.listRecordings({});
		if (seq !== loadSeq) return;
		recordings.value = rows;
		texts.value = {};
		adoptedNow.value = {};
		await loadTranscripts(rows, seq);
	}
	catch {
		if (seq === loadSeq) error.value = "Diktate konnten nicht geladen werden.";
	}
	finally {
		if (seq === loadSeq) loading.value = false;
	}
}

function preview(id: string): string {
	const text = texts.value[id] ?? "";
	if (expanded.value[id] || text.length <= PREVIEW_CHARS) return text;
	return `${text.slice(0, PREVIEW_CHARS).trimEnd()}…`;
}

async function copy(recording: Recording): Promise<void> {
	const text = texts.value[recording.id];
	if (!text) return;
	if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
		notice.value = "Kopieren wird von diesem Browser nicht unterstützt.";
		return;
	}
	try {
		await navigator.clipboard.writeText(text);
		notice.value = null;
		copiedId.value = recording.id;
		clearTimeout(copiedTimer);
		copiedTimer = setTimeout(() => {
			copiedId.value = null;
		}, 2000);
	}
	catch {
		notice.value = "Kopieren nicht möglich. Bitte den Text manuell markieren.";
	}
}

async function setAdopted(recording: Recording, adopted: boolean): Promise<void> {
	const before = recording.adopted_at ?? null;
	recording.adopted_at = adopted ? new Date().toISOString() : null;
	if (adopted) adoptedNow.value = { ...adoptedNow.value, [recording.id]: true };
	savingId.value = recording.id;
	try {
		await store.setAdopted(recording.id, adopted);
	}
	catch {
		recording.adopted_at = before;
		notice.value = "„Übernommen“ konnte nicht gespeichert werden.";
	}
	finally {
		if (savingId.value === recording.id) savingId.value = null;
	}
}

function onAdoptedChange(recording: Recording, event: Event): void {
	const target = event.target;
	if (target instanceof HTMLInputElement) void setAdopted(recording, target.checked);
}

onMounted(() => {
	void load();
});
</script>

<template>
	<section class="desk">
		<header class="desk__bar">
			<h1 class="desk__title">
				Diktate
			</h1>
			<label class="desk__filter">
				<HToggle
					v-model="onlyOpen"
					aria-label="nur offene"
					data-testid="desk-only-open"
				/>
				<span>nur offene</span>
			</label>
			<HButton
				variant="secondary"
				type="button"
				:disabled="loading"
				data-testid="desk-refresh"
				@click="load"
			>
				Aktualisieren
			</HButton>
		</header>

		<p
			v-if="error"
			class="desk__error"
			role="alert"
		>
			{{ error }}
		</p>
		<p
			class="desk__notice"
			:class="{ 'desk__notice--idle': !notice }"
			role="status"
			aria-live="polite"
			data-testid="desk-notice"
		>
			{{ notice }}
		</p>

		<p
			v-if="loading && recordings.length === 0"
			class="desk__hint"
		>
			Diktate werden geladen…
		</p>
		<p
			v-else-if="!loading && visible.length === 0 && !error"
			class="desk__hint"
			data-testid="desk-empty"
		>
			{{ onlyOpen ? "Keine offenen Diktate." : "Noch keine Diktate." }}
		</p>

		<div
			v-if="visible.length > 0"
			class="desk__scroll"
		>
			<table
				class="desk__table"
				data-testid="desk-table"
			>
				<thead>
					<tr>
						<th scope="col">
							Datum
						</th>
						<th scope="col">
							Termin
						</th>
						<th scope="col">
							Titel
						</th>
						<th scope="col">
							Text
						</th>
						<th scope="col">
							<span class="sr-only">Kopieren</span>
						</th>
						<th scope="col">
							Übernommen
						</th>
					</tr>
				</thead>
				<tbody>
					<tr
						v-for="recording in visible"
						:key="recording.id"
						:class="{ 'desk__row--adopted': recording.adopted_at }"
						data-testid="desk-row"
					>
						<td class="desk__date">
							{{ formatDateTime(recording.calendar_event_start || recording.date_created) }}
						</td>
						<td class="desk__termin">
							{{ recording.calendar_event_title || "—" }}
						</td>
						<td class="desk__name">
							<NuxtLink :to="{ name: 'recording-id', params: { id: recording.id } }">
								{{ recording.title }}
							</NuxtLink>
						</td>
						<td class="desk__text">
							<StatusChip
								v-if="recording.status !== 'done'"
								:status="recording.status"
							/>
							<span
								v-else-if="texts[recording.id] === undefined"
								class="desk__muted"
							>Text wird geladen…</span>
							<span
								v-else-if="!texts[recording.id]"
								class="desk__muted"
							>Kein Text</span>
							<template v-else>
								<span class="desk__preview">{{ preview(recording.id) }}</span>
								<button
									v-if="(texts[recording.id] ?? '').length > PREVIEW_CHARS"
									class="desk__more"
									type="button"
									:aria-expanded="!!expanded[recording.id]"
									@click="expanded = { ...expanded, [recording.id]: !expanded[recording.id] }"
								>
									{{ expanded[recording.id] ? "Weniger" : "Mehr" }}
								</button>
							</template>
						</td>
						<td class="desk__copy">
							<HButton
								variant="secondary"
								type="button"
								:disabled="!texts[recording.id]"
								data-testid="desk-copy"
								@click="copy(recording)"
							>
								{{ copiedId === recording.id ? "Kopiert" : "Kopieren" }}
							</HButton>
						</td>
						<td class="desk__adopted">
							<input
								type="checkbox"
								:checked="!!recording.adopted_at"
								:disabled="savingId === recording.id"
								:aria-label="`Übernommen: ${recording.title}`"
								data-testid="desk-adopted"
								@change="onAdoptedChange(recording, $event)"
							>
						</td>
					</tr>
				</tbody>
			</table>
		</div>
	</section>
</template>

<style scoped>
.desk { width:100%; max-width:1400px; margin:0 auto; display:flex; flex-direction:column; gap:12px; padding:16px; }
.desk__bar { display:flex; align-items:center; flex-wrap:wrap; gap:12px 20px; }
.desk__title { flex:1; margin:0; font-family:var(--font-display); font-size:var(--fs-xl); font-weight:600; color:var(--fg1); }
.desk__filter { display:inline-flex; align-items:center; gap:8px; font-size:var(--fs-sm); color:var(--fg2); cursor:pointer; }
.desk__error { margin:0; color:var(--danger); font-size:var(--fs-sm); }
.desk__notice { margin:0; color:var(--fg2); font-size:var(--fs-sm); }
.desk__notice--idle { display:none; }
.desk__hint { margin:0; color:var(--fg3); }
.desk__scroll { overflow-x:auto; background:var(--bg-raised); border:1px solid var(--border); border-radius:var(--r-md); }
.desk__table { width:100%; min-width:900px; border-collapse:collapse; font-size:var(--fs-sm); }
.desk__table th { text-align:left; padding:10px 12px; font-size:var(--fs-xs); font-weight:600; letter-spacing:.04em; text-transform:uppercase; color:var(--fg3); border-bottom:1px solid var(--border); }
.desk__table td { padding:10px 12px; vertical-align:top; border-bottom:1px solid var(--border); color:var(--fg1); }
.desk__table tbody tr:last-child td { border-bottom:none; }
.desk__row--adopted td { color:var(--fg3); }
.desk__date { white-space:nowrap; font-family:var(--font-mono); font-variant-numeric:tabular-nums; }
.desk__termin { min-width:12ch; }
.desk__name a { color:var(--accent-ink); text-decoration:none; font-weight:500; }
.desk__name a:hover { text-decoration:underline; }
.desk__text { width:45%; line-height:1.5; }
.desk__preview { white-space:pre-wrap; }
.desk__muted { color:var(--fg3); }
.desk__more { margin-left:6px; border:none; background:none; padding:0; color:var(--accent-ink); font:inherit; font-weight:600; cursor:pointer; }
.desk__copy :deep(.h-btn) { padding:8px 12px; white-space:nowrap; }
.desk__adopted { text-align:center; }
.desk__adopted input { width:20px; height:20px; cursor:pointer; }
</style>
