import { hasNativeShareTarget, listenForNativeShares } from "~/utils/nativeShare";

/** Capacitor app: routes files shared from other apps into the share-target page. */
export default defineNuxtPlugin(() => {
	if (!hasNativeShareTarget()) return;
	const router = useRouter();
	void listenForNativeShares(async (url) => {
		await router.isReady();
		await router.push(url);
	});
});
