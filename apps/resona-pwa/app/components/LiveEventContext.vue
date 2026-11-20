<script lang="ts">
import { defineComponent } from "vue";
import type { PropType } from "vue";

// Extension point of /live, overridden by layer apps (Resona Verlauf shows the same
// patient's earlier and later appointments here). live.vue renders it above
// (`part="before"`) and below (`part="after"`) the transcript, only when the
// dictation was started from a calendar event. In this app it renders nothing.
export default defineComponent({
	props: {
		part: { type: String as PropType<"before" | "after">, required: true },
		eventId: { type: String, required: true },
	},
	setup: () => () => null,
});
</script>
