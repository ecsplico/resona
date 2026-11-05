<script setup lang="ts">
const props = withDefaults(defineProps<{ modelValue?: boolean }>(), {
	modelValue: false,
});

const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

function toggle() {
	emit("update:modelValue", !props.modelValue);
}
</script>

<template>
	<button
		type="button"
		role="switch"
		class="h-toggle"
		:class="{ 'h-toggle--on': modelValue }"
		:aria-checked="modelValue"
		@click="toggle"
	>
		<span class="h-toggle__knob" />
	</button>
</template>

<style scoped>
.h-toggle { width:50px; height:30px; border-radius:999px; position:relative; border:none; cursor:pointer; background:#D9CBB6; }
.h-toggle--on { background:var(--accent); }
.h-toggle__knob { width:24px; height:24px; border-radius:50%; background:var(--white); box-shadow:0 1px 3px rgba(0,0,0,.2); position:absolute; top:3px; left:3px; }
.h-toggle--on .h-toggle__knob { left:auto; right:3px; }
</style>
