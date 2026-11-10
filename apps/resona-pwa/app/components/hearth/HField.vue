<script setup lang="ts">
withDefaults(
	defineProps<{
		modelValue?: string;
		label?: string;
		type?: string;
		focused?: boolean;
		mono?: boolean;
		placeholder?: string;
	}>(),
	{
		modelValue: "",
		label: undefined,
		type: "text",
		focused: false,
		mono: false,
		placeholder: undefined,
	},
);

defineEmits<{ "update:modelValue": [value: string] }>();
</script>

<template>
	<div class="h-field">
		<label
			v-if="label"
			class="h-field__label"
		>{{ label }}</label>
		<div
			class="h-field__shell"
			:class="{ 'h-field__shell--focused': focused }"
		>
			<slot name="icon" />
			<input
				class="h-field__input"
				:class="{ 'h-field__input--mono': mono }"
				:type="type"
				:placeholder="placeholder"
				:value="modelValue"
				@input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
			>
		</div>
	</div>
</template>

<style scoped>
.h-field__label { display:block; font-size:13px; font-weight:600; color:var(--crust-deep); margin-bottom:7px; }
.h-field__shell { display:flex; align-items:center; gap:10px; border:1.5px solid var(--border-strong); border-radius:8px; padding:12px 14px; background:var(--white); }
.h-field__shell--focused, .h-field__shell:focus-within { border-color:var(--accent); box-shadow:0 0 0 3px var(--focus-ring); }
.h-field__input { flex:1; border:none; background:transparent; font-family:var(--font-body); font-size:15px; color:var(--fg1); outline:none; }
.h-field__input--mono { font-family:var(--font-mono); }
</style>
