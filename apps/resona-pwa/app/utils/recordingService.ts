import { Capacitor } from "@capacitor/core";
import { ForegroundService, Importance, ServiceType } from "@capawesome-team/capacitor-android-foreground-service";

const NOTIFICATION_ID = 3401;
const NOTIFICATION_CHANNEL = "recording";

export interface RecordingServiceText {
	title: string;
	body: string;
}

const RECORDING_TEXT: RecordingServiceText = {
	title: "Resona nimmt auf",
	body: "Die Aufnahme läuft auch bei gesperrtem Bildschirm.",
};

/**
 * Android only: a foreground service of type `microphone` with a persistent
 * notification. It keeps the process (and the native recorder or the `ResonaSpeech`
 * capture) alive and allowed to use the microphone while the screen is locked.
 * Used by the native recorder (`useNativeRecorder`) and native live dictation
 * (`useDeviceSpeech`); the two never run at the same time.
 */
export async function startRecordingService(text: RecordingServiceText = RECORDING_TEXT): Promise<void> {
	if (Capacitor.getPlatform() !== "android") {
		return;
	}
	// Android 13+ hides the notification without this permission; the service runs anyway.
	await ForegroundService.requestPermissions().catch(() => undefined);
	await ForegroundService.createNotificationChannel({
		id: NOTIFICATION_CHANNEL,
		name: "Aufnahme",
		description: "Läuft, solange Resona aufnimmt.",
		importance: Importance.Low,
	}).catch(() => undefined);
	await ForegroundService.startForegroundService({
		id: NOTIFICATION_ID,
		title: text.title,
		body: text.body,
		smallIcon: "ic_stat_resona",
		serviceType: ServiceType.Microphone,
		notificationChannelId: NOTIFICATION_CHANNEL,
		silent: true,
	});
}

export async function stopRecordingService(): Promise<void> {
	if (Capacitor.getPlatform() === "android") {
		await ForegroundService.stopForegroundService().catch(() => undefined);
	}
}
