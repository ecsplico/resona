import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ref, reactive } from "vue";
import { flushPromises } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import SettingsDrawer from "~/components/SettingsDrawer.vue";
import { useLiveSettings } from "~/composables/useLiveSettings";
import { useHubSettings } from "~/composables/useHubSettings";
import { DEVICE_SPEECH_HINTS } from "~/composables/useDeviceSpeech";
import { useTitleModel } from "~/composables/useTitleModel";

const listListenEngines = vi.hoisted(() => vi.fn());
const availability = vi.hoisted(() => vi.fn());
const installPack = vi.hoisted(() => vi.fn());
const listEngines = vi.hoisted(() => vi.fn());
const listCalendars = vi.hoisted(() => vi.fn(async () => []));

mockNuxtImport("useHub", () => () => ({
	listListenEngines,
	listEngines,
}));

const tokenSync = vi.hoisted(() => ({ enabled: vi.fn(() => true), push: vi.fn(), pull: vi.fn() }));
mockNuxtImport("useHubTokenSync", () => () => tokenSync);

mockNuxtImport("useDeviceSpeech", () => () => ({ availability, install: installPack }));

mockNuxtImport("useCalendar", () => () => ({
	status: ref(null),
	settings: reactive({ calendarId: "", query: "", daysBack: 0, daysAhead: 30 }),
	connectUrl: "/api/calendar?provider=google&action=connect",
	listCalendars,
}));

async function openLive() {
	const wrapper = await mountSuspended(SettingsDrawer);
	await wrapper.find("[data-testid=settings-tab-live]").trigger("click");
	await flushPromises();
	return wrapper;
}

describe("SettingsDrawer – Live tab", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		availability.mockResolvedValue("available");
		installPack.mockResolvedValue(true);
		listEngines.mockResolvedValue({ engines: [], default: null });
		const live = useLiveSettings();
		live.correction = false;
		live.correctionEngine = "";
		live.chunkSeconds = 15;
	});

	it("loads listen engines only once the Live tab opens and lists them with cloud marked", async () => {
		listListenEngines.mockResolvedValue({
			engines: [
				{ name: "parakeet", kind: "local", private: true, models: [] },
				{ name: "deepgram", kind: "cloud", private: false, models: [] },
			],
			default: "parakeet",
		});
		const wrapper = await mountSuspended(SettingsDrawer);
		expect(listListenEngines).not.toHaveBeenCalled();
		await wrapper.find("[data-testid=settings-tab-live]").trigger("click");
		await flushPromises();

		const options = wrapper.findAll("[data-testid=settings-live-engine] option");
		expect(options.map(o => o.text())).toEqual(["Hub-Standard (parakeet)", "parakeet", "deepgram (Cloud)"]);
		expect(options[0]!.attributes("value")).toBe("");
		expect(wrapper.find("[data-testid=settings-live-no-engines]").exists()).toBe(false);
		expect(wrapper.findAll("[data-testid=settings-live-source]")).toHaveLength(2);
		expect(wrapper.text()).toContain("Die Tastatur-Diktierfunktion des Telefons funktioniert unabhängig davon im Texteditor.");
	});

	it("labels a missing hub default as 'keiner'", async () => {
		listListenEngines.mockResolvedValue({ engines: [{ name: "x", kind: "local", private: true, models: [] }], default: null });
		const wrapper = await openLive();
		expect(wrapper.find("[data-testid=settings-live-engine] option").text()).toBe("Hub-Standard (keiner)");
	});

	it("shows the no-streaming notice instead of the select for an empty list", async () => {
		listListenEngines.mockResolvedValue({ engines: [], default: null });
		const wrapper = await openLive();
		expect(wrapper.find("[data-testid=settings-live-engine]").exists()).toBe(false);
		expect(wrapper.find("[data-testid=settings-live-no-engines]").text()).toContain(
			"Dieser Hub bietet kein Live-Streaming an – in Einstellungen → Live die lokale Erkennung wählen.",
		);
	});

	it.each([
		["available", "Bereit (lokal)"],
		["downloading", "Wird geladen…"],
		["unavailable", DEVICE_SPEECH_HINTS.unavailable],
		["unsupported", DEVICE_SPEECH_HINTS.unsupported],
	])("maps device availability %s to its status text", async (state, label) => {
		listListenEngines.mockResolvedValue({ engines: [], default: null });
		availability.mockResolvedValue(state);
		const wrapper = await openLive();
		expect(availability).toHaveBeenCalledWith("de");
		expect(wrapper.find("[data-testid=settings-live-device]").text()).toBe(label);
		expect(wrapper.find("[data-testid=settings-live-device-install]").exists()).toBe(false);
	});

	it("offers the language pack download and re-checks after install", async () => {
		listListenEngines.mockResolvedValue({ engines: [], default: null });
		availability.mockResolvedValueOnce("downloadable").mockResolvedValueOnce("available");
		const wrapper = await openLive();
		expect(wrapper.find("[data-testid=settings-live-device]").text()).toBe("Sprachpaket verfügbar");

		await wrapper.find("[data-testid=settings-live-device-install]").trigger("click");
		await flushPromises();
		expect(installPack).toHaveBeenCalledWith("de");
		expect(availability).toHaveBeenCalledTimes(2);
		expect(wrapper.find("[data-testid=settings-live-device]").text()).toBe("Bereit (lokal)");
		expect(wrapper.find("[data-testid=settings-live-device-install]").exists()).toBe(false);
	});

	it("persists the correction toggle and shows its controls only when on", async () => {
		listListenEngines.mockResolvedValue({ engines: [], default: null });
		const wrapper = await openLive();
		expect(wrapper.text()).toContain("Mit besserem Modell korrigieren");
		expect(wrapper.find("[data-testid=settings-live-correction-engine]").exists()).toBe(false);
		expect(wrapper.find("[data-testid=settings-live-chunk]").exists()).toBe(false);

		await wrapper.find("[data-testid=settings-live-correction]").trigger("click");
		await flushPromises();
		expect(useLiveSettings().correction).toBe(true);
		expect(JSON.parse(window.localStorage.getItem("resona.live.settings")!).correction).toBe(true);
		expect(wrapper.find("[data-testid=settings-live-correction-engine]").exists()).toBe(true);
		expect(wrapper.find("[data-testid=settings-live-chunk]").exists()).toBe(true);
	});

	it("offers only available audio engines for correction, after Hub-Standard", async () => {
		listListenEngines.mockResolvedValue({ engines: [], default: null });
		listEngines.mockResolvedValue({
			engines: [
				{ name: "whisper", kind: "local", job_kind: "audio", private: true, available: true, models: [], candidates: [] },
				{ name: "offline", kind: "local", job_kind: "audio", private: true, available: false, models: [], candidates: [] },
				{ name: "docling", kind: "local", job_kind: "document", private: true, available: true, models: [], candidates: [] },
				{ name: "openai", kind: "cloud", job_kind: "audio", private: false, available: true, models: [], candidates: [] },
			],
			default: "whisper",
		});
		useLiveSettings().correction = true;
		const wrapper = await openLive();
		expect(listEngines).toHaveBeenCalledTimes(1);
		const options = wrapper.findAll("[data-testid=settings-live-correction-engine] option");
		expect(options.map(o => o.attributes("value"))).toEqual(["", "whisper", "openai"]);
		expect(options[0]!.text()).toBe("Hub-Standard");

		await wrapper.find("[data-testid=settings-live-correction-engine]").setValue("openai");
		expect(useLiveSettings().correctionEngine).toBe("openai");
	});

	it("keeps Hub-Standard as the only option when the engine list fails", async () => {
		listListenEngines.mockResolvedValue({ engines: [], default: null });
		listEngines.mockRejectedValue(new Error("offline"));
		useLiveSettings().correction = true;
		const wrapper = await openLive();
		expect(wrapper.findAll("[data-testid=settings-live-correction-engine] option")).toHaveLength(1);
	});

	it.each([
		["3", 8],
		["45", 30],
		["20", 20],
		["12.7", 12],
	])("clamps chunk length %s to %i seconds", async (input, expected) => {
		listListenEngines.mockResolvedValue({ engines: [], default: null });
		useLiveSettings().correction = true;
		const wrapper = await openLive();
		const chunk = wrapper.find("[data-testid=settings-live-chunk]");
		expect(wrapper.text()).toContain("Abschnittslänge (Sekunden)");
		(chunk.element as HTMLInputElement).value = input;
		await chunk.trigger("change");
		await flushPromises();
		expect(useLiveSettings().chunkSeconds).toBe(expected);
		expect((chunk.element as HTMLInputElement).value).toBe(String(expected));
	});
});

describe("SettingsDrawer – on-device title suggestions", () => {
	type Monitor = (m: EventTarget) => void;
	let modelState: string;

	function stubModel(create: (opts: { monitor?: Monitor }) => Promise<{ destroy: () => void }>) {
		const model = {
			availability: vi.fn(async () => modelState),
			create: vi.fn(create),
		};
		vi.stubGlobal("LanguageModel", model);
		return model;
	}

	const state = (w: VueWrapper) => w.find("[data-testid=settings-title-model-state]").text();
	const installButton = (w: VueWrapper) => w.find("[data-testid=settings-title-model-install]");

	beforeEach(() => {
		vi.clearAllMocks();
		listListenEngines.mockResolvedValue({ engines: [], default: null });
		availability.mockResolvedValue("available");
		listEngines.mockResolvedValue({ engines: [], default: null });
		const tm = useTitleModel();
		tm.state.value = null;
		tm.installing.value = false;
		tm.progress.value = null;
		tm.failed.value = false;
		modelState = "downloadable";
	});

	afterEach(() => {
		vi.unstubAllGlobals();
		vi.useRealTimers();
		useLiveSettings().correction = false;
	});

	it("explains that it is not possible here when the browser has no model (the test environment)", async () => {
		const wrapper = await openLive();
		expect(state(wrapper)).toContain("Auf diesem Gerät nicht möglich");
		expect(state(wrapper)).toContain("Chrome am Desktop");
		expect(installButton(wrapper).exists()).toBe(false);
		expect(wrapper.find("[data-testid=settings-title-model-progress]").exists()).toBe(false);
	});

	it("explains hardware limits when the model is unavailable, without a button", async () => {
		modelState = "unavailable";
		const model = stubModel(async () => ({ destroy: vi.fn() }));
		const wrapper = await openLive();
		expect(state(wrapper)).toContain("Auf diesem Gerät nicht möglich");
		expect(state(wrapper)).toContain("Speicherplatz");
		expect(installButton(wrapper).exists()).toBe(false);
		expect(model.create).not.toHaveBeenCalled();
	});

	it("reports an active model without a button", async () => {
		modelState = "available";
		const model = stubModel(async () => ({ destroy: vi.fn() }));
		const wrapper = await openLive();
		expect(state(wrapper)).toMatch(/^Aktiv/);
		expect(installButton(wrapper).exists()).toBe(false);
		expect(model.create).not.toHaveBeenCalled();
	});

	it("downloads only on the click, shows progress while the app stays usable, then reports it active", async () => {
		const created = Promise.withResolvers<{ destroy: () => void }>();
		let monitor: EventTarget | null = null;
		const model = stubModel(async (opts) => {
			monitor = new EventTarget();
			opts.monitor?.(monitor);
			return created.promise;
		});
		const wrapper = await openLive();
		expect(state(wrapper)).toBe("Aktivierbar");
		expect(wrapper.text()).toContain("mehrere GB Speicherplatz");
		expect(wrapper.text()).toContain("Aufnehmen funktioniert auch ohne diesen Schritt unverändert");
		expect(model.create).not.toHaveBeenCalled();

		await installButton(wrapper).trigger("click");
		expect(model.create).toHaveBeenCalledTimes(1);
		monitor!.dispatchEvent(Object.assign(new Event("downloadprogress"), { loaded: 0.42 }));
		await flushPromises();
		expect(state(wrapper)).toBe("Modell wird geladen… 42 %");
		expect((wrapper.find("[data-testid=settings-title-model-progress]").element as HTMLProgressElement).value).toBe(0.42);
		expect(installButton(wrapper).exists()).toBe(false);

		// Still usable meanwhile: another setting works, and a reopened drawer shows the download.
		await wrapper.find("[data-testid=settings-live-correction]").trigger("click");
		expect(useLiveSettings().correction).toBe(true);
		wrapper.unmount();
		const reopened = await openLive();
		expect(state(reopened)).toBe("Modell wird geladen… 42 %");

		modelState = "available";
		created.resolve({ destroy: vi.fn() });
		await flushPromises();
		expect(state(reopened)).toMatch(/^Aktiv/);
		expect(reopened.find("[data-testid=settings-title-model-progress]").exists()).toBe(false);
		expect(model.create).toHaveBeenCalledTimes(1);
	});

	it("a failed download leaves a hint and can be started again", async () => {
		const model = stubModel(async () => {
			throw new DOMException("aborted", "AbortError");
		});
		const wrapper = await openLive();
		await installButton(wrapper).trigger("click");
		await flushPromises();
		expect(wrapper.find("[data-testid=settings-title-model-failed]").text()).toContain("fehlgeschlagen oder wurde abgebrochen");
		expect(wrapper.find("[data-testid=settings-title-model-failed]").text()).toContain("Aufnehmen und Speichern funktionieren unverändert");
		expect(state(wrapper)).toBe("Aktivierbar");
		expect(installButton(wrapper).text()).toBe("Erneut versuchen");

		await installButton(wrapper).trigger("click");
		await flushPromises();
		expect(model.create).toHaveBeenCalledTimes(2);
	});

	it("follows a download it did not start until it ends", async () => {
		vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
		modelState = "downloading";
		const model = stubModel(async () => ({ destroy: vi.fn() }));
		const wrapper = await openLive();
		expect(state(wrapper)).toBe("Modell wird geladen…");
		expect(installButton(wrapper).exists()).toBe(false);

		modelState = "available";
		await vi.advanceTimersByTimeAsync(5000);
		await flushPromises();
		expect(state(wrapper)).toMatch(/^Aktiv/);
		expect(model.create).not.toHaveBeenCalled();
	});
});

// Real runtimeConfig, flag set and restored per test (see useDataStore.spec.ts).
describe("SettingsDrawer – calendar flag", () => {
	let original: unknown;

	beforeEach(() => {
		vi.clearAllMocks();
		original = useRuntimeConfig().public.calendarEnabled;
	});

	afterEach(() => {
		useRuntimeConfig().public.calendarEnabled = original as boolean;
	});

	it("hides the Kalender tab and never lists calendars when off", async () => {
		useRuntimeConfig().public.calendarEnabled = false;
		const wrapper = await mountSuspended(SettingsDrawer);
		expect(wrapper.find("[data-testid=settings-tab-calendar]").exists()).toBe(false);
		await wrapper.find("[data-testid=settings-tab-live]").trigger("click");
		await flushPromises();
		expect(listCalendars).not.toHaveBeenCalled();
	});

	it("shows the Kalender tab and loads calendars once when on", async () => {
		useRuntimeConfig().public.calendarEnabled = "true" as unknown as boolean;
		const wrapper = await mountSuspended(SettingsDrawer);
		const calTab = wrapper.find("[data-testid=settings-tab-calendar]");
		expect(calTab.exists()).toBe(true);
		await calTab.trigger("click");
		await flushPromises();
		await wrapper.find("[data-testid=settings-tab-hub]").trigger("click");
		await wrapper.find("[data-testid=settings-tab-calendar]").trigger("click");
		await flushPromises();
		expect(listCalendars).toHaveBeenCalledTimes(1);
	});
});

describe("SettingsDrawer – modal behaviour", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		listListenEngines.mockResolvedValue({ engines: [], default: null });
		availability.mockResolvedValue("available");
		listEngines.mockResolvedValue({ engines: [], default: null });
	});

	it("closes on Fertig without reloading the page", async () => {
		const reload = vi.spyOn(window.location, "reload").mockImplementation(() => {});
		const wrapper = await mountSuspended(SettingsDrawer);
		await wrapper.find("[data-testid=settings-done]").trigger("click");
		expect(wrapper.emitted("close")).toHaveLength(1);
		expect(reload).not.toHaveBeenCalled();
		reload.mockRestore();
	});

	it("closes on Escape", async () => {
		const wrapper = await mountSuspended(SettingsDrawer);
		document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
		expect(wrapper.emitted("close")).toHaveLength(1);
	});

	it("moves focus into the drawer on open and back to the opener on close", async () => {
		const opener = document.createElement("button");
		document.body.appendChild(opener);
		opener.focus();
		const wrapper = await mountSuspended(SettingsDrawer, { attachTo: document.body });
		await flushPromises();
		expect(document.activeElement).toBe(wrapper.find("[data-testid=settings-close]").element);
		wrapper.unmount();
		expect(document.activeElement).toBe(opener);
		opener.remove();
	});

	it("moves the selected tab with the arrow keys, Home and End", async () => {
		const wrapper = await mountSuspended(SettingsDrawer, { attachTo: document.body });
		const selected = () => wrapper.find("[role=tab][aria-selected=true]");
		const tablist = wrapper.find("[role=tablist]");
		expect(selected().attributes("data-testid")).toBe("settings-tab-hub");
		expect(wrapper.find("[data-testid=settings-tab-live]").attributes("tabindex")).toBe("-1");

		await tablist.trigger("keydown", { key: "ArrowRight" });
		expect(selected().attributes("data-testid")).toBe("settings-tab-live");
		expect(selected().attributes("tabindex")).toBe("0");
		expect(document.activeElement).toBe(selected().element);

		await tablist.trigger("keydown", { key: "ArrowLeft" });
		expect(selected().attributes("data-testid")).toBe("settings-tab-hub");

		await tablist.trigger("keydown", { key: "ArrowLeft" });
		const tabs = wrapper.findAll("[role=tab]");
		expect(selected().element).toBe(tabs[tabs.length - 1]!.element);

		await tablist.trigger("keydown", { key: "Home" });
		expect(selected().attributes("data-testid")).toBe("settings-tab-hub");
		await tablist.trigger("keydown", { key: "End" });
		expect(selected().element).toBe(tabs[tabs.length - 1]!.element);
		wrapper.unmount();
	});
});

describe("SettingsDrawer – hub token account sync", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		tokenSync.enabled.mockReturnValue(true);
		tokenSync.push.mockResolvedValue(true);
		useHubSettings().token = "";
	});

	afterEach(() => {
		useHubSettings().token = "";
	});

	async function enterToken(wrapper: VueWrapper, value: string): Promise<void> {
		const input = wrapper.find("[data-testid=settings-hub-token]");
		await input.setValue(value);
		await input.trigger("change");
		await flushPromises();
	}

	it("stores the entered token on the account (directus mode) and says so", async () => {
		const wrapper = await mountSuspended(SettingsDrawer);
		expect(wrapper.find("[data-testid=settings-hub-token-hint]").text()).toContain("in deinem Konto");
		await enterToken(wrapper, "new-token");
		expect(useHubSettings().token).toBe("new-token");
		expect(tokenSync.push).toHaveBeenCalledWith("new-token");
		expect(wrapper.find("[data-testid=settings-hub-token-sync]").text()).toContain("im Konto gespeichert");
	});

	it("reports an account failure but keeps the device token", async () => {
		tokenSync.push.mockRejectedValue(new Error("403"));
		const wrapper = await mountSuspended(SettingsDrawer);
		await enterToken(wrapper, "tok");
		expect(useHubSettings().token).toBe("tok");
		expect(wrapper.find("[data-testid=settings-hub-token-sync]").text()).toContain("nur auf diesem Gerät");
	});

	it("does not touch the account in standalone mode", async () => {
		tokenSync.enabled.mockReturnValue(false);
		const wrapper = await mountSuspended(SettingsDrawer);
		expect(wrapper.find("[data-testid=settings-hub-token-hint]").exists()).toBe(false);
		await enterToken(wrapper, "tok");
		expect(tokenSync.push).not.toHaveBeenCalled();
		expect(wrapper.find("[data-testid=settings-hub-token-sync]").exists()).toBe(false);
		expect(wrapper.text()).toContain("Der Token wird nur auf diesem Gerät gespeichert.");
	});
});
