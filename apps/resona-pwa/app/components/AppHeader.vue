<script setup lang="ts">
import { computed } from "vue";
import { isCalendarEnabled } from "#shared/calendarEnabled";

type Section = "recordings" | "documents" | "calendar";

const route = useRoute();
const { wide } = useShell();
const settings = useSettingsDrawer();
const calendarEnabled = isCalendarEnabled(useRuntimeConfig().public.calendarEnabled);

const TABS = [
	{ key: "recordings", label: "Aufnahmen", to: "/", testid: "nav-recordings" },
	{ key: "documents", label: "Dokumente", to: "/documents", testid: "nav-documents" },
	{ key: "calendar", label: "Kalender", to: "/calendar", testid: "nav-calendar" },
] as const satisfies readonly { key: Section; label: string; to: string; testid: string }[];
const tabs = TABS.filter(t => t.key !== "calendar" || calendarEnabled);

// Pages that bring their own back bar; on phones the shell header would only
// duplicate it, on wide screens the header stays for section navigation.
const OWN_BACK_BAR = [/^\/recording\/[^/]+/, /^\/documents\/[^/]+/, /^\/live\/?$/, /^\/debug\/?$/, /^\/share-target\/?$/];
const visible = computed(() => wide.value || !OWN_BACK_BAR.some(re => re.test(route.path)));

const active = computed<Section | null>(() => {
	const path = route.path;
	if (path === "/" || path.startsWith("/recording/")) {
		return "recordings";
	}
	if (path === "/documents" || path.startsWith("/documents/")) {
		return "documents";
	}
	if (path === "/calendar") {
		return "calendar";
	}
	return null;
});
</script>

<template>
	<header
		v-if="visible"
		class="app-header"
		data-testid="app-header"
	>
		<div class="app-header__inner">
			<NuxtLink
				class="app-header__brand"
				to="/"
			>
				Resona
			</NuxtLink>
			<ServerStatus class="app-header__status" />
			<nav
				class="h-tabs app-header__tabs"
				aria-label="Bereiche"
			>
				<NuxtLink
					v-for="tab in tabs"
					:key="tab.key"
					class="h-tab app-header__tab"
					:class="{ 'h-tab--active': active === tab.key }"
					:to="tab.to"
					:aria-current="active === tab.key ? 'page' : undefined"
					:data-testid="tab.testid"
				>
					{{ tab.label }}
				</NuxtLink>
			</nav>
			<button
				class="app-header__cog"
				type="button"
				aria-label="Einstellungen"
				aria-haspopup="dialog"
				data-testid="nav-settings"
				@click="settings.show()"
			>
				<svg
					width="22"
					height="22"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2"
					stroke-linecap="round"
					stroke-linejoin="round"
					aria-hidden="true"
				>
					<circle
						cx="12"
						cy="12"
						r="3"
					/>
					<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
				</svg>
			</button>
		</div>
	</header>
</template>

<style scoped>
/* Phone: row 1 wordmark · status · cog, row 2 the section tabs. */
.app-header { position:sticky; top:0; z-index:20; background:var(--bg); padding-top:env(safe-area-inset-top); }
.app-header__inner { display:grid; grid-template-columns:auto 1fr auto; grid-template-areas:"brand status cog" "tabs tabs tabs"; align-items:center; column-gap:8px; padding:0 16px; }
.app-header__brand { grid-area:brand; padding:10px 0; font-family:var(--font-display); font-size:var(--fs-lg); font-weight:600; line-height:1.2; color:var(--fg1); text-decoration:none; border-radius:var(--r-xs); }
.app-header__status { grid-area:status; justify-self:start; }
.app-header__tabs { grid-area:tabs; }
.app-header__cog { grid-area:cog; width:40px; height:40px; margin-right:-8px; border:none; border-radius:var(--r-full); background:none; color:var(--fg2); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
.app-header__cog:hover { background:var(--cream-deep); color:var(--fg1); }
.app-header__cog:active { background:var(--crust-soft); }

/* Wide: one row, wordmark · tabs · … · status · cog; the tab underline sits on the header edge. */
@media (min-width:960px) {
	.app-header { border-bottom:1px solid var(--border); }
	.app-header__inner { grid-template-columns:auto auto 1fr auto auto; grid-template-areas:"brand tabs . status cog"; column-gap:16px; min-height:56px; padding:0 24px; }
	.app-header__brand { font-size:var(--fs-xl); margin-right:16px; }
	.app-header__tabs { align-self:stretch; border-bottom:none; gap:24px; }
	.app-header__tab { display:inline-flex; align-items:center; margin-bottom:0; }
}
</style>
