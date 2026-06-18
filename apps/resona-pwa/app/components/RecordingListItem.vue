<script setup lang="ts">
import type { Recording } from "~/types/directus";

const props = defineProps<{ recording: Recording }>();

defineEmits<{ open: []; copy: [] }>();

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
		:title="recording.title"
		:meta="meta"
		:status="recording.status"
		@select="$emit('open')"
	>
		<template #actions>
			<HIconButton
				:size="30"
				tone="cream"
				data-testid="copy-action"
				@click.stop="$emit('copy')"
			>
				<svg
					width="14"
					height="14"
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
			</HIconButton>
		</template>
	</HListRow>
</template>
