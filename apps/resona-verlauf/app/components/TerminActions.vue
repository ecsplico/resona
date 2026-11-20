<script setup lang="ts">
import { computed } from "vue";
import type { HMenuItem } from "~/components/hearth/HMenu.vue";
import { liveQuery } from "~~/shared/termine";
import type { TerminEvent, TerminMark } from "~~/shared/termine";

// Per-appointment actions: "Diktieren" (mic, straight to /live) and the "Aktionen"
// menu (mark as erledigt / abgesagt, or remove the mark). Clicks never reach the
// row underneath, so they do not open the appointment.
const props = withDefaults(defineProps<{ event: TerminEvent; dictate?: boolean }>(), { dictate: true });
const emit = defineEmits<{ mark: [status: TerminMark]; unmark: [] }>();

type Action = TerminMark | "unmark";

const items = computed<HMenuItem<Action>[]>(() => (props.event.mark
	? [{ id: "unmark", label: "Markierung aufheben" }]
	: [
			{ id: "erledigt", label: "Als erledigt markieren" },
			{ id: "abgesagt", label: "Termin abgesagt" },
		]));

function select(action: Action): void {
	if (action === "unmark") emit("unmark");
	else emit("mark", action);
}

function startDictation(): void {
	void navigateTo({ path: "/live", query: liveQuery(props.event) });
}
</script>

<template>
	<div
		class="termin-actions"
		@click.stop
	>
		<HIconButton
			v-if="dictate"
			type="button"
			:size="40"
			aria-label="Diktieren"
			title="Diktieren"
			data-testid="termin-dictate"
			@click="startDictation"
		>
			<svg
				width="18"
				height="18"
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
					y="2"
					width="6"
					height="12"
					rx="3"
				/>
				<path d="M5 10v1a7 7 0 0 0 14 0v-1" />
				<path d="M12 18v4" />
			</svg>
		</HIconButton>
		<HMenu
			label="Aktionen"
			:items="items"
			@select="select"
		/>
	</div>
</template>

<style scoped>
.termin-actions { display:flex; align-items:center; gap:4px; flex-shrink:0; }
</style>
