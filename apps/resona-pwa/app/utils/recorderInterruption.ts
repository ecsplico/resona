/**
 * Why a recording stopped by itself. Web recorder: the track went silent, ended, or the
 * page was hidden. Native recorder (Capacitor app): a phone call (or another app) took the
 * microphone.
 */
export type InterruptReason = "muted" | "ended" | "hidden" | "call";

const INTERRUPT_TEXT: Record<InterruptReason, string> = {
	muted: "Das Mikrofon liefert keinen Ton mehr, z. B. wegen eines Anrufs.",
	ended: "Das Mikrofon wurde getrennt.",
	hidden: "Die App war im Hintergrund.",
	call: "Ein Anruf oder eine andere App hat das Mikrofon übernommen.",
};

/** German notice for an interruption: the reason, then `detail` or that the audio so far is kept. */
export function describeInterruption(reason: InterruptReason, detail: string | null): string {
	return `${INTERRUPT_TEXT[reason]} ${detail ?? "Bisher Aufgenommenes bleibt erhalten."}`;
}
