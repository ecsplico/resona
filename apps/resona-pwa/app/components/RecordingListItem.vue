<script setup lang="ts">
import type { Recording } from "~/types/directus";
import type { HMenuItem } from "~/components/hearth/HMenu.vue";

/** The ⋯ menu's actions, handed to the parent as one `action` event. */
export type RowAction = "share" | "export" | "delete";

const props = defineProps<{ recording: Recording; selected?: boolean }>();

const emit = defineEmits<{ "open": []; "copy": []; "menu-open": []; "action": [action: RowAction] }>();

// Export only makes sense once there is a transcript to write.
const menuItems = computed<HMenuItem<RowAction>[]>(() => [
	{ id: "share", label: "Teilen" },
	...(props.recording.status === "done" ? [{ id: "export" as const, label: "Als Markdown exportieren" }] : []),
	{ id: "delete", label: "Löschen", danger: true },
]);

function formatDuration(seconds?: number): string {
	if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
		return "";
	}
	const total = Math.round(seconds);
	const minutes = Math.floor(total / 60);
	const secs = total % 60;
	return `${minutes}:${secs.toString().padStart(2, "0")}`;
}

function formatDate(iso?: string): string {
	if (!iso) return "";
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return "";
	return date.toLocaleDateString("de-DE", {
		day: "2-digit",
		month: "2-digit",
		year: "numeric",
	});
}

function formatMeta(recording: Recording): string {
	const parts = [
		formatDate(recording.date_created),
		formatDuration(recording.duration_seconds),
		recording.language ? recording.language.toUpperCase() : "",
	].filter(part => part.length > 0);
	return parts.join(" · ");
}

const meta = computed(() => formatMeta(props.recording));
</script>

<template>
	<HListRow
		:title="recording.title || 'Aufnahme'"
		:meta="meta"
		:status="recording.status"
		:selected="selected"
		@select="$emit('open')"
	>
		<template #actions>
			<button
				type="button"
				class="row-copy"
				aria-label="Kopieren"
				title="Kopieren"
				data-testid="copy-action"
				@click.stop="$emit('copy')"
			>
				<svg
					width="16"
					height="16"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2"
					stroke-linecap="round"
					stroke-linejoin="round"
					aria-hidden="true"
				>
					<rect
						x="9"
						y="9"
						width="12"
						height="12"
						rx="2"
					/>
					<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
				</svg>
			</button>
			<HMenu
				label="Weitere Aktionen"
				:items="menuItems"
				@open="emit('menu-open')"
				@select="emit('action', $event)"
			/>
		</template>
	</HListRow>
</template>

<style scoped>
.row-copy { width:40px; height:40px; display:inline-flex; align-items:center; justify-content:center; border:0; border-radius:var(--r-full); background:transparent; color:var(--fg3); cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
.row-copy:hover { background:var(--bg-sunken); color:var(--fg1); }
.row-copy:active { background:var(--crust-soft); }
.row-copy:disabled { opacity:0.4; cursor:not-allowed; }
</style>
