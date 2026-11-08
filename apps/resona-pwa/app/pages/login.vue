<script setup lang="ts">
import { onMounted, ref } from "vue";

definePageMeta({ layout: false });

const { login, loginWithSso, completeSso } = useDirectus();
const ssoProvider = useRuntimeConfig().public.ssoProvider;

const email = ref("");
const password = ref("");
const submitting = ref(false);
const error = ref("");

onMounted(async () => {
	if (useRoute().query.sso !== "1") return;
	if (await completeSso()) await navigateTo("/");
	else error.value = "Anmeldung fehlgeschlagen";
});

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
				role="alert"
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
			<HButton
				v-if="ssoProvider"
				class="login__submit"
				variant="secondary"
				type="button"
				@click="loginWithSso(ssoProvider, '/login?sso=1')"
			>
				Mit Keycloak anmelden
			</HButton>
		</form>
	</div>
</template>

<style scoped>
/* One centered card on the cream page at every width. */
.login { min-height:100dvh; display:flex; align-items:center; justify-content:center; padding:24px 16px calc(24px + env(safe-area-inset-bottom)); background:var(--bg); }
.login__card { display:flex; flex-direction:column; align-items:center; gap:16px; width:100%; max-width:400px; box-sizing:border-box; padding:32px 24px 28px; border:1px solid var(--border); border-radius:var(--r-lg); background:var(--bg-raised); }
.login__icon { width:56px; height:56px; border-radius:var(--r-md); background:var(--accent); color:var(--white); display:flex; align-items:center; justify-content:center; }
.login__icon svg { width:28px; height:28px; }
.login__title { margin:4px 0 0; font-family:var(--font-display); font-size:var(--fs-2xl); font-weight:600; line-height:1.2; color:var(--fg1); }
.login__subtitle { margin:-8px 0 8px; font-size:var(--fs-sm); color:var(--fg3); text-align:center; }
.login__field { width:100%; }
.login__error { margin:0; align-self:stretch; padding:10px 12px; border-radius:var(--r-sm); background:var(--tomato-tint); color:var(--danger); font-size:var(--fs-sm); }
.login__submit { width:100%; min-height:48px; margin-top:8px; transition:background-color var(--motion-fast) var(--motion-ease); }
.login__submit:hover:not(:disabled) { background:var(--accent-hover); }
@media (min-width:600px) { .login__card { padding:40px 36px 32px; } }
</style>
