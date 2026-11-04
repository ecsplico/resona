<template>
	<!-- Injects <link rel="manifest"> into the head. Without it the browser never
		sees the web app manifest, so the app is not detected/installable as a PWA. -->
	<VitePwaManifest />
	<div class="device-viewport">
		<div class="device-frame">
			<NuxtLayout>
				<NuxtPage />
			</NuxtLayout>
		</div>
	</div>
</template>

<style scoped>
.device-viewport { min-height:100vh; }
.device-frame { min-height:100vh; }

/* Desktop: render the whole app inside a phone-shaped frame, full window height. */
@media (min-width:768px) {
	.device-viewport {
		box-sizing:border-box;
		height:100dvh;
		min-height:100dvh;
		padding:32px 0;
		display:flex;
		justify-content:center;
		background:var(--bg-sunken);
	}
	.device-frame {
		position:relative;
		height:100%;
		width:auto;
		aspect-ratio:9/19.5;
		max-width:min(430px,90vw);
		min-height:0;
		overflow-y:auto;
		overscroll-behavior:contain;
		background:var(--bg);
		border:10px solid var(--ink);
		border-radius:44px;
		box-shadow:var(--shadow-deep);
		scrollbar-width:none;
	}
	.device-frame::-webkit-scrollbar { display:none; }
	.device-frame::before {
		content:"";
		position:sticky;
		top:10px;
		display:block;
		width:90px;
		height:26px;
		margin:0 auto;
		border-radius:var(--r-full);
		background:var(--ink);
		z-index:10;
	}
}
</style>
