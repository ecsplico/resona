import type { Router } from "vue-router";

/**
 * Entries that are never a sensible place to go back to: the login screen and
 * the share-target landing page only forward to the real screen.
 */
const TRANSIENT = /^\/(?:login|share-target)\/?$/;

function bare(path: string): string {
	return path.replace(/[?#].*$/, "");
}

/**
 * Path of the history entry before the current one, or null when the app was
 * entered directly (deep link, reload into a fresh tab, PWA launch). vue-router
 * records it in `history.state.back`, and it survives reloads.
 */
export function previousPath(router: Pick<Router, "options">): string | null {
	const back = router.options.history.state?.back;
	return typeof back === "string" && !TRANSIENT.test(bare(back)) ? back : null;
}

/**
 * One rule for every way of going back, so the header arrows, the browser/Android
 * back button and the edge swipe always agree:
 *
 * - `back(fallback)`: the in-app arrow. Same as browser back when the app has an
 *   earlier entry, otherwise it replaces the current entry with `fallback` (the
 *   right list), so a second back never returns to the page that was just left.
 * - `leaveTo(path)`: leaves a page for good (after save/delete/cancel). Pops the
 *   history when the previous entry already is `path` (the list keeps its scroll
 *   and search), otherwise replaces; it never pushes, so the finished page cannot
 *   be reached again with back.
 */
export function useBackNav() {
	const router = useRouter();

	async function back(fallback: string): Promise<void> {
		if (previousPath(router) !== null) {
			router.back();
			return;
		}
		await navigateTo(fallback, { replace: true });
	}

	async function leaveTo(path: string): Promise<void> {
		const previous = previousPath(router);
		if (previous !== null && bare(previous) === path) {
			router.back();
			return;
		}
		await navigateTo(path, { replace: true });
	}

	return { back, leaveTo };
}
