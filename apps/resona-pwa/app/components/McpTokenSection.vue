<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { mcpSnippets, mcpUrl } from "~/utils/mcpSetup";

// Einstellungen → MCP: the personal token for MCP clients (useMcpToken). Mounted
// only while its tab is open, so a freshly created token is gone once the user
// leaves the tab or closes the settings.
const mcp = useMcpToken();
const { exists, shown, busy, error } = mcp;
const origin = typeof window === "undefined" ? "" : window.location.origin;
const url = mcpUrl(origin);
const snippets = computed(() => (shown.value ? mcpSnippets(origin, shown.value) : []));

const confirming = ref<"replace" | "revoke" | null>(null);
const copyNote = ref<{ key: string; text: string } | null>(null);

const stateText = computed(() => {
	if (exists.value === null) {
		return busy.value ? "Wird geprüft…" : "Unbekannt";
	}
	return exists.value
		? "Ein Token ist aktiv. Er wird aus Sicherheitsgründen nicht noch einmal angezeigt."
		: "Kein Token angelegt.";
});

onMounted(() => {
	void mcp.check();
});

async function copy(key: string, text: string): Promise<void> {
	copyNote.value = null;
	try {
		await navigator.clipboard.writeText(text);
		copyNote.value = { key, text: "Kopiert." };
	}
	catch {
		copyNote.value = { key, text: "Kopieren nicht möglich – bitte markieren und selbst kopieren." };
	}
}

async function confirmAction(): Promise<void> {
	const action = confirming.value;
	confirming.value = null;
	copyNote.value = null;
	await (action === "revoke" ? mcp.revoke() : mcp.create());
}
</script>

<template>
	<div
		class="mcp"
		data-testid="settings-mcp"
	>
		<p
			v-if="mcp.availability === 'standalone'"
			class="mcp__sub"
			data-testid="settings-mcp-unavailable"
		>
			MCP-Zugriff gibt es nur mit Konto auf einem Server – im Standalone-Modus liegen die Diktate nur in diesem Browser.
		</p>
		<p
			v-else-if="mcp.availability === 'app'"
			class="mcp__sub"
			data-testid="settings-mcp-unavailable"
		>
			Den MCP-Zugang richtest du in der Web-App im Browser ein.
		</p>

		<template v-else>
			<p class="mcp__hint">
				Mit einem persönlichen Token können KI-Programme wie Claude Code, Claude Desktop oder Cursor deine Diktate über MCP ({{ url }}) auflisten, durchsuchen und lesen.
			</p>
			<p
				class="mcp__notice"
				data-testid="settings-mcp-rights"
			>
				Der Token hat dieselben Rechte wie deine Anmeldung: Wer ihn kennt, kann alles sehen und ändern, was du selbst sehen und ändern darfst. Gib ihn nicht weiter und widerrufe ihn, wenn du ihn nicht mehr brauchst.
			</p>

			<div class="mcp__field">
				<span class="mcp__label">Status</span>
				<span
					class="mcp__readonly"
					role="status"
					data-testid="settings-mcp-state"
				>{{ stateText }}</span>
			</div>

			<div
				v-if="shown"
				class="mcp__fresh"
				data-testid="settings-mcp-fresh"
			>
				<p class="mcp__notice">
					Kopiere den Token jetzt: Er wird nur dieses eine Mal angezeigt.
				</p>
				<div class="mcp__field">
					<span class="mcp__label">Dein Token</span>
					<code
						class="mcp__code"
						data-testid="settings-mcp-token"
					>{{ shown }}</code>
					<HButton
						variant="secondary"
						data-testid="settings-mcp-copy-token"
						@click="copy('token', shown!)"
					>
						Token kopieren
					</HButton>
					<p
						v-if="copyNote?.key === 'token'"
						class="mcp__sub"
						role="status"
					>
						{{ copyNote.text }}
					</p>
				</div>

				<div
					v-for="s in snippets"
					:key="s.key"
					class="mcp__field"
				>
					<span class="mcp__label">{{ s.label }}</span>
					<span class="mcp__sub">{{ s.where }}</span>
					<pre
						class="mcp__code"
						:data-testid="`settings-mcp-snippet-${s.key}`"
					>{{ s.text }}</pre>
					<HButton
						variant="secondary"
						:data-testid="`settings-mcp-copy-${s.key}`"
						@click="copy(s.key, s.text)"
					>
						Kopieren
					</HButton>
					<p
						v-if="copyNote?.key === s.key"
						class="mcp__sub"
						role="status"
					>
						{{ copyNote.text }}
					</p>
				</div>

				<HButton
					variant="secondary"
					data-testid="settings-mcp-hide"
					@click="mcp.hide()"
				>
					Token ausblenden
				</HButton>
			</div>

			<div
				v-if="confirming"
				class="mcp__confirm"
				role="group"
				aria-labelledby="settings-mcp-confirm-text"
				data-testid="settings-mcp-confirm"
			>
				<p
					id="settings-mcp-confirm-text"
					class="mcp__hint"
				>
					{{ confirming === "revoke"
						? "Token widerrufen? Programme, die ihn nutzen, verlieren sofort den Zugriff."
						: "Token ersetzen? Der bisherige Token hört sofort auf zu funktionieren; Programme, die ihn nutzen, musst du mit dem neuen Token neu einrichten." }}
				</p>
				<div class="mcp__row">
					<HButton
						variant="primary"
						:disabled="busy"
						data-testid="settings-mcp-confirm-yes"
						@click="confirmAction"
					>
						{{ confirming === "revoke" ? "Widerrufen" : "Ersetzen" }}
					</HButton>
					<HButton
						variant="secondary"
						data-testid="settings-mcp-confirm-no"
						@click="confirming = null"
					>
						Abbrechen
					</HButton>
				</div>
			</div>

			<div
				v-else
				class="mcp__row"
			>
				<HButton
					v-if="exists === false"
					variant="primary"
					:disabled="busy"
					data-testid="settings-mcp-create"
					@click="mcp.create()"
				>
					Token erstellen
				</HButton>
				<template v-else-if="exists">
					<HButton
						variant="secondary"
						:disabled="busy"
						data-testid="settings-mcp-replace"
						@click="confirming = 'replace'"
					>
						Ersetzen
					</HButton>
					<HButton
						variant="secondary"
						:disabled="busy"
						data-testid="settings-mcp-revoke"
						@click="confirming = 'revoke'"
					>
						Widerrufen
					</HButton>
				</template>
				<HButton
					v-else
					variant="secondary"
					:disabled="busy"
					data-testid="settings-mcp-recheck"
					@click="mcp.check()"
				>
					Erneut prüfen
				</HButton>
			</div>

			<p
				v-if="error"
				class="mcp__notice"
				role="alert"
				data-testid="settings-mcp-error"
			>
				{{ error }}
			</p>
		</template>
	</div>
</template>

<style scoped>
.mcp { display:flex; flex-direction:column; gap:20px; }
.mcp__hint { margin:0; font-size:var(--fs-sm); line-height:1.5; color:var(--fg1); }
.mcp__notice { margin:0; padding:12px 14px; border-radius:var(--r-md); background:var(--butter-soft); color:var(--butter-ink); font-size:var(--fs-sm); line-height:1.5; }
.mcp__sub { margin:0; font-size:var(--fs-xs); line-height:1.5; color:var(--fg3); }
.mcp__field { display:flex; flex-direction:column; gap:8px; }
.mcp__label { font-size:var(--fs-sm); font-weight:600; color:var(--fg2); }
.mcp__readonly { font-size:var(--fs-sm); color:var(--fg2); }
.mcp__fresh { display:flex; flex-direction:column; gap:20px; }
.mcp__code { margin:0; padding:10px 12px; border-radius:var(--r-sm); background:var(--bg-sunken); font-family:var(--font-mono); font-size:var(--fs-xs); line-height:1.5; color:var(--fg1); white-space:pre-wrap; word-break:break-all; user-select:all; }
.mcp__confirm { display:flex; flex-direction:column; gap:12px; padding:12px 14px; border:1.5px solid var(--border-strong); border-radius:var(--r-md); }
.mcp__row { display:flex; flex-wrap:wrap; gap:12px; }
.mcp__row > * { flex:1; }
</style>
