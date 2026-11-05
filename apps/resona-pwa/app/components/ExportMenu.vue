<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from "vue";

const emit = defineEmits<{ txt: []; md: [] }>();

const open = ref(false);
const root = ref<HTMLElement | null>(null);
const trigger = ref<HTMLButtonElement | null>(null);
const menu = ref<HTMLElement | null>(null);

function items(): HTMLElement[] {
	return Array.from(menu.value?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? []);
}

async function show(): Promise<void> {
	open.value = true;
	await nextTick();
	items()[0]?.focus();
}

function close(returnFocus: boolean): void {
	open.value = false;
	if (returnFocus) {
		trigger.value?.focus();
	}
}

function toggle(): void {
	if (open.value) {
		close(false);
	}
	else {
		void show();
	}
}

function choose(kind: "txt" | "md"): void {
	close(true);
	if (kind === "txt") emit("txt");
	else emit("md");
}

function onMenuKey(e: KeyboardEvent): void {
	const list = items();
	const index = list.indexOf(document.activeElement as HTMLElement);
	if (e.key === "Escape") {
		e.preventDefault();
		close(true);
	}
	else if (e.key === "ArrowDown") {
		e.preventDefault();
		list[(index + 1) % list.length]?.focus();
	}
	else if (e.key === "ArrowUp") {
		e.preventDefault();
		list[(index - 1 + list.length) % list.length]?.focus();
	}
	else if (e.key === "Tab") {
		close(false);
	}
}

function onDocPointer(e: PointerEvent): void {
	if (root.value && !root.value.contains(e.target as Node)) {
		close(false);
	}
}

watch(open, (isOpen) => {
	if (isOpen) document.addEventListener("pointerdown", onDocPointer);
	else document.removeEventListener("pointerdown", onDocPointer);
});

onUnmounted(() => document.removeEventListener("pointerdown", onDocPointer));
</script>

<template>
	<div
		ref="root"
		class="xmenu"
	>
		<button
			ref="trigger"
			type="button"
			class="xmenu__trigger"
			aria-label="Exportieren"
			title="Exportieren"
			aria-haspopup="menu"
			:aria-expanded="open"
			data-testid="export-menu"
			@click="toggle"
			@keydown.escape="close(true)"
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
				<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
				<polyline points="7 10 12 15 17 10" />
				<line
					x1="12"
					y1="15"
					x2="12"
					y2="3"
				/>
			</svg>
		</button>
		<div
			v-if="open"
			ref="menu"
			class="xmenu__list"
			role="menu"
			aria-label="Exportieren"
			@keydown="onMenuKey"
		>
			<button
				type="button"
				role="menuitem"
				class="xmenu__item"
				data-testid="export-txt"
				@click="choose('txt')"
			>
				Als Text (.txt)
			</button>
			<button
				type="button"
				role="menuitem"
				class="xmenu__item"
				data-testid="export-md"
				@click="choose('md')"
			>
				Als Markdown (.md)
			</button>
		</div>
	</div>
</template>

<style scoped>
.xmenu { position:relative; display:inline-flex; }
.xmenu__trigger { width:38px; height:38px; border:none; border-radius:50%; background:transparent; color:var(--crust-deep); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; transition:background var(--motion-fast,150ms) ease-out; }
.xmenu__trigger:hover, .xmenu__trigger[aria-expanded="true"] { background:var(--cream-deep); }
.xmenu__trigger:active { background:var(--crust-soft); }
.xmenu__list { position:absolute; right:0; top:calc(100% + 6px); z-index:20; min-width:200px; display:flex; flex-direction:column; padding:6px; background:var(--white); border:1px solid var(--border); border-radius:12px; box-shadow:var(--shadow-lift); }
.xmenu__item { text-align:left; padding:10px 12px; border:none; border-radius:8px; background:transparent; font-family:var(--font-body); font-size:14px; color:var(--fg1); cursor:pointer; }
.xmenu__item:hover, .xmenu__item:focus-visible { background:var(--cream-deep); }
</style>
