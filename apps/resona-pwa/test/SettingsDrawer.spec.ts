import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref, reactive } from "vue";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import SettingsDrawer from "~/components/SettingsDrawer.vue";
import { useLiveSettings } from "~/composables/useLiveSettings";

const listListenEngines = vi.hoisted(() => vi.fn());
const availability = vi.hoisted(() => vi.fn());
const installPack = vi.hoisted(() => vi.fn());
const listEngines = vi.hoisted(() => vi.fn());

mockNuxtImport("useHub", () => () => ({
	listListenEngines,
	listEngines,
}));

mockNuxtImport("useDeviceSpeech", () => () => ({ availability, install: installPack }));

mockNuxtImport("useCalendar", () => () => ({
	status: ref(null),
	settings: reactive({ calendarId: "", query: "", daysBack: 0, daysAhead: 30 }),
	connectUrl: "/api/calendar?provider=google&action=connect",
	listCalendars: vi.fn(async () => []),
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
		["unavailable", "Nicht verfügbar in diesem Browser"],
		["unsupported", "Nicht verfügbar in diesem Browser"],
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
