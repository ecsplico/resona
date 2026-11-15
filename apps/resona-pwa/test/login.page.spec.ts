import { describe, it, expect, vi, beforeEach } from "vitest";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import LoginPage from "~/pages/login.vue";

const login = vi.hoisted(() => vi.fn(async () => {}));
const directus = vi.hoisted(() => ({ ssoSupported: true }));

mockNuxtImport("useDirectus", () => () => ({ login, ssoSupported: directus.ssoSupported }));

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

describe("login page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		directus.ssoSupported = true;
		useRuntimeConfig().public.ssoProvider = "";
	});

	it("renders the email + password fields and the Anmelden button", async () => {
		const w = await mountSuspended(LoginPage);
		const inputs = w.findAll("input");
		expect(inputs.length).toBeGreaterThanOrEqual(2);
		expect(w.find("input[type=\"email\"]").exists()).toBe(true);
		expect(w.find("input[type=\"password\"]").exists()).toBe(true);
		expect(w.text()).toContain("Anmelden");
	});

	it("calls login with the entered credentials on submit", async () => {
		const w = await mountSuspended(LoginPage);
		await w.find("input[type=\"email\"]").setValue("a@b.de");
		await w.find("input[type=\"password\"]").setValue("pw");
		await w.find("form").trigger("submit.prevent");
		expect(login).toHaveBeenCalledWith("a@b.de", "pw");
	});

	it("web build: offers the Keycloak button when a provider is configured", async () => {
		useRuntimeConfig().public.ssoProvider = "keycloak";
		const w = await mountSuspended(LoginPage);
		expect(w.text()).toContain("Mit Keycloak anmelden");
		expect(w.find("[data-testid=\"login-sso-hint\"]").exists()).toBe(false);
	});

	it("app: replaces the Keycloak button with a hint to sign in with password", async () => {
		useRuntimeConfig().public.ssoProvider = "keycloak";
		directus.ssoSupported = false;
		const w = await mountSuspended(LoginPage);
		expect(w.text()).not.toContain("Mit Keycloak anmelden");
		expect(w.find("[data-testid=\"login-sso-hint\"]").text()).toContain("in der App noch nicht möglich");
	});
});
