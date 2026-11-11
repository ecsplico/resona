// @ts-check
import withNuxt from "./.nuxt/eslint.config.mjs";

export default withNuxt(
	// Capacitor native projects: generated build output and copied web assets.
	{ ignores: ["android/**", "ios/**"] },
	{ rules: { "vue/multi-word-component-names": "off" } },
);
