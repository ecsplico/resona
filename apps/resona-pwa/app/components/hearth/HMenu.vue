<script setup lang="ts" generic="T extends string">
import { nextTick, onBeforeUnmount, ref, useId } from "vue";

export interface HMenuItem<Id extends string = string> {
	id: Id;
	label: string;
	/** Destructive action (Löschen): drawn in the danger colour. */
	danger?: boolean;
}

const props = defineProps<{ label: string; items: HMenuItem<T>[] }>();
// `open` fires when the menu opens, so the owner can prepare what an item needs
// synchronously (e.g. the text `navigator.share` must get inside the tap).
const emit = defineEmits<{ open: []; select: [id: T] }>();

const GAP = 4;
const EDGE = 8;

const open = ref(false);
const root = ref<HTMLElement | null>(null);
const trigger = ref<HTMLButtonElement | null>(null);
const menu = ref<HTMLElement | null>(null);
const position = ref<Record<string, string>>({});
const menuId = useId();
const triggerId = useId();

function menuItems(): HTMLElement[] {
	return Array.from(menu.value?.querySelectorAll<HTMLElement>("[role=\"menuitem\"]") ?? []);
}

function focusItem(index: number): void {
	const els = menuItems();
	if (els.length === 0) return;
	els[(index + els.length) % els.length]!.focus();
}

// The menu is `position: fixed` so the grouped list's `overflow: hidden` cannot clip it.
// It opens below the trigger, or above when there is no room left in the viewport.
function place(): void {
	const button = trigger.value;
	if (!button || typeof window === "undefined") return;
	const rect = button.getBoundingClientRect();
	const right = `${Math.max(EDGE, window.innerWidth - rect.right)}px`;
	const height = menu.value?.offsetHeight ?? 0;
	position.value = rect.bottom + GAP + height <= window.innerHeight - EDGE
		? { top: `${rect.bottom + GAP}px`, right }
		: { bottom: `${Math.max(EDGE, window.innerHeight - rect.top + GAP)}px`, right };
}

function onOutside(event: Event): void {
	const target = event.target as Node | null;
	if (target && root.value?.contains(target)) return;
	close(false);
}

// Scrolling would leave a fixed menu floating away from its row.
function onViewportChange(event: Event): void {
	const target = event.target as Node | null;
	if (target && menu.value?.contains(target)) return;
	close(false);
}

function listen(on: boolean): void {
	if (typeof document === "undefined") return;
	if (on) {
		document.addEventListener("pointerdown", onOutside, true);
		window.addEventListener("scroll", onViewportChange, true);
		window.addEventListener("resize", onViewportChange);
	}
	else {
		document.removeEventListener("pointerdown", onOutside, true);
		window.removeEventListener("scroll", onViewportChange, true);
		window.removeEventListener("resize", onViewportChange);
	}
}

async function openMenu(focus: "first" | "last"): Promise<void> {
	if (open.value) return;
	open.value = true;
	listen(true);
	emit("open");
	await nextTick();
	place();
	focusItem(focus === "first" ? 0 : -1);
}

function close(returnFocus: boolean): void {
	if (!open.value) return;
	open.value = false;
	listen(false);
	if (returnFocus) trigger.value?.focus();
}

function toggle(): void {
	if (open.value) close(true);
	else void openMenu("first");
}

function onTriggerKeydown(event: KeyboardEvent): void {
	if (event.key === "ArrowDown") {
		event.preventDefault();
		void openMenu("first");
	}
	else if (event.key === "ArrowUp") {
		event.preventDefault();
		void openMenu("last");
	}
}

function onMenuKeydown(event: KeyboardEvent): void {
	const els = menuItems();
	const current = els.indexOf(document.activeElement as HTMLElement);
	switch (event.key) {
		case "ArrowDown":
			event.preventDefault();
			focusItem(current + 1);
			break;
		case "ArrowUp":
			event.preventDefault();
			focusItem(current < 0 ? -1 : current - 1);
			break;
		case "Home":
			event.preventDefault();
			focusItem(0);
			break;
		case "End":
			event.preventDefault();
			focusItem(-1);
			break;
		case "Escape":
			event.preventDefault();
			close(true);
			break;
		case "Tab":
			close(true);
			break;
	}
}

function choose(item: HMenuItem<T>): void {
	close(true);
	emit("select", item.id);
}

onBeforeUnmount(() => listen(false));
</script>

<template>
	<!-- Clicks never reach the row underneath: opening the menu must not open the recording. -->
	<div
		ref="root"
		class="h-menu"
		@click.stop
	>
		<button
			:id="triggerId"
			ref="trigger"
			type="button"
			class="h-menu__trigger"
			aria-haspopup="menu"
			:aria-expanded="open ? 'true' : 'false'"
			:aria-controls="open ? menuId : undefined"
			:aria-label="props.label"
			:title="props.label"
			data-testid="menu-trigger"
			@click="toggle"
			@keydown="onTriggerKeydown"
		>
			<svg
				width="18"
				height="18"
				viewBox="0 0 24 24"
				fill="currentColor"
				aria-hidden="true"
			>
				<circle
					cx="5"
					cy="12"
					r="2"
				/>
				<circle
					cx="12"
					cy="12"
					r="2"
				/>
				<circle
					cx="19"
					cy="12"
					r="2"
				/>
			</svg>
		</button>
		<div
			v-if="open"
			:id="menuId"
			ref="menu"
			class="h-menu__list"
			role="menu"
			:aria-labelledby="triggerId"
			:style="position"
			data-testid="menu"
			@keydown="onMenuKeydown"
		>
			<button
				v-for="item in props.items"
				:key="item.id"
				type="button"
				role="menuitem"
				tabindex="-1"
				class="h-menu__item"
				:class="{ 'h-menu__item--danger': item.danger }"
				:data-testid="`menu-${item.id}`"
				@click="choose(item)"
			>
				{{ item.label }}
			</button>
		</div>
	</div>
</template>

<style scoped>
.h-menu { position:relative; display:inline-flex; }
.h-menu__trigger { width:40px; height:40px; display:inline-flex; align-items:center; justify-content:center; border:0; border-radius:var(--r-full); background:transparent; color:var(--fg3); cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
.h-menu__trigger:hover, .h-menu__trigger[aria-expanded="true"] { background:var(--bg-sunken); color:var(--fg1); }
.h-menu__trigger:active { background:var(--crust-soft); }
.h-menu__trigger:focus-visible { outline:none; box-shadow:0 0 0 3px var(--focus-ring); }
.h-menu__list { position:fixed; z-index:40; min-width:200px; display:flex; flex-direction:column; padding:6px; border:1px solid var(--border); border-radius:var(--r-md); background:var(--bg-raised); box-shadow:var(--shadow-deep); }
.h-menu__item { min-height:44px; padding:10px 12px; border:0; border-radius:var(--r-sm); background:none; color:var(--fg1); font-family:var(--font-body); font-size:var(--fs-md); text-align:left; cursor:pointer; }
.h-menu__item:hover, .h-menu__item:focus-visible { background:var(--row-hover); outline:none; }
.h-menu__item:active { background:var(--row-active); }
.h-menu__item--danger { color:var(--danger); }
</style>
