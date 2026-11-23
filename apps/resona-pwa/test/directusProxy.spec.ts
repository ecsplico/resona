import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { createApp, defineEventHandler, toWebHandler } from "h3";
import { EXPIRE_HOST_ONLY_REFRESH_COOKIE, proxyDirectus } from "~~/server/utils/directusProxy";

// What Directus sends at the end of the Keycloak callback with
// REFRESH_TOKEN_COOKIE_DOMAIN=ecsplico.de.
const DIRECTUS_COOKIE = "directus_refresh_token=new-token; Max-Age=2592000; Domain=ecsplico.de; Path=/; HttpOnly; Secure; SameSite=Lax";
const CALLBACK = "/auth/login/keycloak/callback?state=s&code=c";

let directus: Server;
let base: string;

beforeAll(async () => {
	directus = createServer((_req, res) => {
		res.writeHead(302, { "location": "https://resona.ecsplico.de/login?sso=1", "set-cookie": [DIRECTUS_COOKIE] });
		res.end();
	});
	const listening = Promise.withResolvers<undefined>();
	directus.listen(0, "127.0.0.1", () => listening.resolve(undefined));
	await listening.promise;
	base = `http://127.0.0.1:${(directus.address() as AddressInfo).port}`;
});

afterAll(async () => {
	const closed = Promise.withResolvers<undefined>();
	directus.close(() => closed.resolve(undefined));
	await closed.promise;
});

function proxy(cookieDomain: string) {
	return toWebHandler(createApp().use(defineEventHandler(event =>
		proxyDirectus(event, event.path.split("?")[0]!, { base, cookieDomain }),
	)));
}

describe("/directus proxy, SSO callback", () => {
	it("with a cookie domain: expires the stale host-only refresh cookie after Directus' own", async () => {
		const res = await proxy("ecsplico.de")(new Request(`https://resona.ecsplico.de${CALLBACK}`));
		expect(res.status).toBe(302);
		expect(res.headers.get("location")).toBe("https://resona.ecsplico.de/login?sso=1");
		const cookies = res.headers.getSetCookie();
		expect(cookies).toEqual([DIRECTUS_COOKIE, EXPIRE_HOST_ONLY_REFRESH_COOKIE]);
		// No Domain attribute: targets only the host-only cookie, not Directus' new one.
		expect(cookies[1]).toMatch(/^directus_refresh_token=; /);
		expect(cookies[1]).toContain("Max-Age=0");
		expect(cookies[1]).not.toMatch(/domain=/i);
	});

	it("without a cookie domain: leaves the host-only cookie alone (it is the live one)", async () => {
		const res = await proxy("")(new Request(`https://resona.ecsplico.de${CALLBACK}`));
		expect(res.headers.getSetCookie()).toEqual([DIRECTUS_COOKIE]);
	});
});
