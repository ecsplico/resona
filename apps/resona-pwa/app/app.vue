<template>
	<!-- Injects <link rel="manifest"> into the head. Without it the browser never
		sees the web app manifest, so the app is not detected/installable as a PWA. -->
	<VitePwaManifest />
	<NuxtLayout>
		<NuxtPage />
	</NuxtLayout>
</template>
