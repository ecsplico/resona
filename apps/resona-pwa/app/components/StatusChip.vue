<script setup lang="ts">
type Status = "pending" | "transcribing" | "done" | "error";

defineProps<{ status: Status }>();

const STATUS_TONE: Record<Status, "success" | "warning" | "neutral" | "danger"> = {
	done: "success",
	transcribing: "warning",
	pending: "neutral",
	error: "danger",
};

const STATUS_LABEL: Record<Status, string> = {
	done: "Fertig",
	transcribing: "Transkribiert…",
	pending: "Wartet",
	error: "Fehler",
};
</script>

<template>
	<HChip
		:tone="STATUS_TONE[status]"
		:dot="status === 'transcribing'"
	>
		{{ STATUS_LABEL[status] }}
	</HChip>
</template>
