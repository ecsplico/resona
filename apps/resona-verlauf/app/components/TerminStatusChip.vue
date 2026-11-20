<script setup lang="ts">
import { TERMIN_MARK_LABELS } from "~~/shared/termine";
import type { TerminMark } from "~~/shared/termine";

// Status of one appointment: dictated (with count), manually marked, or open.
defineProps<{ dictations: number; mark?: TerminMark }>();
</script>

<template>
	<HChip
		v-if="dictations > 0"
		tone="success"
		dot
		data-testid="termin-status-done"
	>
		Diktiert{{ dictations > 1 ? ` (${dictations})` : "" }}
	</HChip>
	<HChip
		v-else-if="mark"
		:tone="mark === 'erledigt' ? 'success' : 'neutral'"
		:data-testid="`termin-status-${mark}`"
	>
		{{ TERMIN_MARK_LABELS[mark] }}
	</HChip>
	<HChip
		v-else
		tone="neutral"
		data-testid="termin-status-open"
	>
		Offen
	</HChip>
</template>
