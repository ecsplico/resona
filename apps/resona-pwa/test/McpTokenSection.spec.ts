import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import McpTokenSection from "~/components/McpTokenSection.vue";

const request = vi.hoisted(() => vi.fn());
mockNuxtImport("useDirectus", () => () => ({ client: { request } }));

interface Command { path: string; method?: string; body?: string; params?: Record<string, unknown> }
const commands = (): Command[] => request.mock.calls.map(([cmd]) => (cmd as () => Command)());
const patches = (): Command[] => commands().filter(c => c.method === "PATCH");

async function mount() {
	const wrapper = await mountSuspended(McpTokenSection);
	await flushPromises();
	return wrapper;
}

describe("McpTokenSection (directus mode)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("says in plain words that the token has the rights of the login", async () => {
		request.mockResolvedValueOnce([]);
		const wrapper = await mount();
		expect(wrapper.find("[data-testid=settings-mcp-rights]").text()).toContain("dieselben Rechte wie deine Anmeldung");
		expect(wrapper.find("[data-testid=settings-mcp-state]").text()).toBe("Kein Token angelegt.");
	});

	it("creates a token and shows it once with setup snippets for this origin", async () => {
		request.mockResolvedValueOnce([]).mockResolvedValueOnce({ id: "u1" });
		const wrapper = await mount();
		await wrapper.find("[data-testid=settings-mcp-create]").trigger("click");
		await flushPromises();

		const { token } = JSON.parse(patches()[0]!.body!) as { token: string };
		expect(wrapper.find("[data-testid=settings-mcp-token]").text()).toBe(token);
		const url = `${window.location.origin}/mcp`;
		expect(wrapper.find("[data-testid=settings-mcp-snippet-claude-code]").text())
			.toBe(`claude mcp add --transport http resona ${url} --header "Authorization: Bearer ${token}"`);
		expect(wrapper.find("[data-testid=settings-mcp-snippet-claude-desktop]").text()).toContain(`Bearer ${token}`);
		expect(wrapper.find("[data-testid=settings-mcp-snippet-cursor]").text()).toContain(url);

		await wrapper.find("[data-testid=settings-mcp-hide]").trigger("click");
		expect(wrapper.text()).not.toContain(token);
		expect(wrapper.find("[data-testid=settings-mcp-state]").text()).toContain("Ein Token ist aktiv");
	});

	it("does not show the token after the section is opened again", async () => {
		request.mockResolvedValueOnce([]).mockResolvedValueOnce({ id: "u1" }).mockResolvedValueOnce([{ id: "u1" }]);
		const first = await mount();
		await first.find("[data-testid=settings-mcp-create]").trigger("click");
		await flushPromises();
		const { token } = JSON.parse(patches()[0]!.body!) as { token: string };
		first.unmount();

		const again = await mount();
		expect(again.text()).not.toContain(token);
		expect(again.find("[data-testid=settings-mcp-fresh]").exists()).toBe(false);
		expect(again.find("[data-testid=settings-mcp-replace]").exists()).toBe(true);
	});

	it("copies the token to the clipboard", async () => {
		const writeText = vi.fn(async () => {});
		Object.defineProperty(globalThis.navigator, "clipboard", { value: { writeText }, configurable: true });
		request.mockResolvedValueOnce([]).mockResolvedValueOnce({ id: "u1" });
		const wrapper = await mount();
		await wrapper.find("[data-testid=settings-mcp-create]").trigger("click");
		await flushPromises();
		await wrapper.find("[data-testid=settings-mcp-copy-token]").trigger("click");
		await flushPromises();
		const { token } = JSON.parse(patches()[0]!.body!) as { token: string };
		expect(writeText).toHaveBeenCalledWith(token);
		expect(wrapper.text()).toContain("Kopiert.");
	});

	it("replaces only after confirmation", async () => {
		request.mockResolvedValueOnce([{ id: "u1" }]).mockResolvedValue({ id: "u1" });
		const wrapper = await mount();
		expect(wrapper.find("[data-testid=settings-mcp-create]").exists()).toBe(false);

		await wrapper.find("[data-testid=settings-mcp-replace]").trigger("click");
		expect(wrapper.find("[data-testid=settings-mcp-confirm]").text()).toContain("hört sofort auf zu funktionieren");
		await wrapper.find("[data-testid=settings-mcp-confirm-no]").trigger("click");
		expect(patches()).toHaveLength(0);

		await wrapper.find("[data-testid=settings-mcp-replace]").trigger("click");
		await wrapper.find("[data-testid=settings-mcp-confirm-yes]").trigger("click");
		await flushPromises();
		const [write] = patches();
		expect(write!.path).toBe("/users/me");
		const { token } = JSON.parse(write!.body!) as { token: string };
		expect(wrapper.find("[data-testid=settings-mcp-token]").text()).toBe(token);
	});

	it("revokes only after confirmation", async () => {
		request.mockResolvedValueOnce([{ id: "u1" }]).mockResolvedValue({ id: "u1" });
		const wrapper = await mount();
		await wrapper.find("[data-testid=settings-mcp-revoke]").trigger("click");
		expect(patches()).toHaveLength(0);
		expect(wrapper.find("[data-testid=settings-mcp-confirm]").text()).toContain("verlieren sofort den Zugriff");
		await wrapper.find("[data-testid=settings-mcp-confirm-yes]").trigger("click");
		await flushPromises();
		expect(JSON.parse(patches()[0]!.body!)).toEqual({ token: null });
		expect(wrapper.find("[data-testid=settings-mcp-state]").text()).toBe("Kein Token angelegt.");
		expect(wrapper.find("[data-testid=settings-mcp-create]").exists()).toBe(true);
	});

	it("shows the error when storing fails", async () => {
		request.mockResolvedValueOnce([]).mockRejectedValueOnce(new Error("403"));
		const wrapper = await mount();
		await wrapper.find("[data-testid=settings-mcp-create]").trigger("click");
		await flushPromises();
		expect(wrapper.find("[data-testid=settings-mcp-error]").text()).toContain("nicht gespeichert");
		expect(wrapper.find("[data-testid=settings-mcp-fresh]").exists()).toBe(false);
	});
});

describe("McpTokenSection (standalone mode)", () => {
	let original: string;

	beforeEach(() => {
		vi.clearAllMocks();
		original = useRuntimeConfig().public.dataMode;
		useRuntimeConfig().public.dataMode = "standalone";
	});

	afterEach(() => {
		useRuntimeConfig().public.dataMode = original;
	});

	it("hides the token controls and explains why in one line", async () => {
		const wrapper = await mount();
		expect(wrapper.find("[data-testid=settings-mcp-unavailable]").text()).toContain("Standalone-Modus");
		expect(wrapper.find("[data-testid=settings-mcp-rights]").exists()).toBe(false);
		expect(wrapper.find("[data-testid=settings-mcp-create]").exists()).toBe(false);
		expect(request).not.toHaveBeenCalled();
	});
});
