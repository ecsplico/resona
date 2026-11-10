import { ref } from "vue";
import type { Ref } from "vue";
import { installTitleModel, titleModelAvailability } from "~/utils/titleRefinement";
import type { TitleModelAvailability } from "~/utils/titleRefinement";

// Module-level: a download keeps running and reporting progress after the
// settings drawer closes, and a reopened drawer shows it again.
const state = ref<TitleModelAvailability | null>(null);
const installing = ref(false);
const progress = ref<number | null>(null);
const failed = ref(false);

/**
 * Device state of the browser language model behind on-device title
 * suggestions (Einstellungen → Live), plus the one user-triggered download.
 * The live page needs nothing from here: `refineTitle` checks availability on
 * every recording, so a finished download applies to the next one.
 */
export function useTitleModel(): {
	state: Ref<TitleModelAvailability | null>;
	installing: Ref<boolean>;
	/** Downloaded fraction 0–1 while this app's download runs, else null. */
	progress: Ref<number | null>;
	/** The last download failed or was aborted. */
	failed: Ref<boolean>;
	check: () => Promise<void>;
	install: () => Promise<void>;
} {
	const language = useRuntimeConfig().public.defaultLanguage;

	async function check(): Promise<void> {
		if (installing.value) {
			return;
		}
		state.value = await titleModelAvailability(language);
	}

	// Called directly from the click: installTitleModel starts the download
	// before its first await, inside the user activation the browser requires.
	async function install(): Promise<void> {
		if (installing.value) {
			return;
		}
		failed.value = false;
		installing.value = true;
		progress.value = 0;
		state.value = "downloading";
		try {
			await installTitleModel(language, (loaded) => {
				progress.value = loaded;
			});
		}
		catch {
			failed.value = true;
		}
		finally {
			installing.value = false;
			progress.value = null;
		}
		await check();
	}

	return { state, installing, progress, failed, check, install };
}
