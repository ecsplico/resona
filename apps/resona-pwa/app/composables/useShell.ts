import { computed, inject, provide, ref } from "vue";
import type { ComputedRef, InjectionKey, Ref } from "vue";

export interface Shell {
	/** Viewport is at least 960 px wide; evaluated once in the layout. */
	wide: Ref<boolean>;
	/** The layout renders the recordings pane itself (desktop two-pane workspace). */
	paneInLayout: ComputedRef<boolean>;
}

const SHELL_KEY: InjectionKey<Shell> = Symbol("resona-shell");

/** Routes the desktop workspace shows next to the recordings pane. */
const PANE_ROUTE = /^\/(?:recording\/[^/]+\/?)?$/;

/** Called by the default layout only. */
export function provideShell(wide: Ref<boolean>): Shell {
	const route = useRoute();
	const shell: Shell = {
		wide,
		paneInLayout: computed(() => wide.value && PANE_ROUTE.test(route.path)),
	};
	provide(SHELL_KEY, shell);
	return shell;
}

/**
 * Shell context for pages and components. Without the layout (login, specs that
 * mount a page directly) it falls back to phone behaviour.
 */
export function useShell(): Shell {
	return inject(SHELL_KEY, () => ({ wide: ref(false), paneInLayout: computed(() => false) }), true);
}

/** Settings drawer state; the drawer itself is rendered by the default layout. */
export function useSettingsDrawer(): { open: Ref<boolean>; show: () => void; hide: () => void } {
	const open = useState("settings-drawer-open", () => false);
	return {
		open,
		show: () => {
			open.value = true;
		},
		hide: () => {
			open.value = false;
		},
	};
}

/**
 * Cross-component request to reload the recordings list (e.g. after the detail
 * page changed a recording); `RecordingsPane` watches `signal`.
 */
export function useRecordingsRefresh(): { signal: Readonly<Ref<number>>; request: () => void } {
	const signal = useState<number>("recordings-refresh", () => 0);
	return {
		signal,
		request: () => {
			signal.value++;
		},
	};
}
