import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref, reactive } from "vue";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import SettingsDrawer from "~/components/SettingsDrawer.vue";

const listListenEngines = vi.hoisted(() => vi.fn());
const availability = vi.hoisted(() => vi.fn());
const installPack = vi.hoisted(() => vi.fn());

mockNuxtImport("useHub", () => () => ({
	listListenEngines,
	listEngines: vi.fn(async () => ({ engines: [], default: null })),
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
});
