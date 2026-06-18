<script setup lang="ts">
const props = defineProps<{ size?: number; tone?: "glass" | "cream" }>();
</script>

<template>
	<button
		class="h-iconbtn"
		:class="`h-iconbtn--${props.tone ?? 'cream'}`"
		:style="{ width: (props.size ?? 38) + 'px', height: (props.size ?? 38) + 'px' }"
	>
		<slot />
	</button>
</template>

<style scoped>
.h-iconbtn { border:none; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; }
.h-iconbtn--glass { background:rgba(255,255,255,0.16); color:#fff; }
.h-iconbtn--cream { background:var(--cream-deep); color:var(--crust-deep); }
</style>
