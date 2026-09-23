<script setup lang="ts">
import { ref } from "vue";

definePageMeta({ layout: false });

const { login } = useDirectus();

const email = ref("");
const password = ref("");
const submitting = ref(false);
const error = ref("");

async function submit(): Promise<void> {
	if (submitting.value) return;
	submitting.value = true;
	error.value = "";
	try {
		await login(email.value, password.value);
		await navigateTo("/");
	}
	catch {
		error.value = "Anmeldung fehlgeschlagen";
	}
	finally {
		submitting.value = false;
	}
}
</script>

<template>
	<div class="login">
		<form
			class="login__card"
			@submit.prevent="submit"
		>
			<div class="login__icon">
				<svg
					width="34"
					height="34"
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
					<path d="M5 10a7 7 0 0 0 14 0" />
					<line
						x1="12"
						y1="17"
						x2="12"
						y2="22"
					/>
				</svg>
			</div>
			<h1 class="login__title">
				Resona
			</h1>
			<p class="login__subtitle">
				Melde dich an, um fortzufahren
			</p>

			<HField
				v-model="email"
				class="login__field"
				type="email"
				label="E-Mail"
			/>
			<HField
				v-model="password"
				class="login__field"
				type="password"
				label="Passwort"
			/>

			<p
				v-if="error"
				class="login__error"
			>
				{{ error }}
			</p>

			<HButton
				class="login__submit"
				variant="primary"
				type="submit"
				:disabled="submitting"
				@click="submit"
			>
				Anmelden
			</HButton>
		</form>
	</div>
</template>

<style scoped>
.login { min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; background:var(--bg); }
@media (min-width:768px) { .login { min-height:100%; } }
.login__card { display:flex; flex-direction:column; align-items:center; gap:14px; width:100%; max-width:380px; }
.login__icon { width:78px; height:78px; border-radius:20px; background:var(--accent); color:var(--white); display:flex; align-items:center; justify-content:center; box-shadow:var(--shadow-lift); }
.login__title { font-family:var(--font-display); font-size:34px; font-weight:600; color:var(--fg1); margin:6px 0 0; }
.login__subtitle { font-size:15px; color:var(--fg3); margin:0 0 8px; text-align:center; }
.login__field { width:100%; }
.login__error { color:var(--danger); font-size:14px; margin:0; align-self:flex-start; }
.login__submit { width:100%; margin-top:6px; }
</style>
