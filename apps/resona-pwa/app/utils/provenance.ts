import type { Provenance } from "~/types/directus";

function formatSeconds(seconds: number): string {
	if (!Number.isFinite(seconds)) return "";
	return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`.replace(".", ",");
}

/**
 * Expert-mode lines for a result's hub provenance: one per processing stage
 * (engine, model, accelerator, duration), prefixed on the first line with the
 * worker that ran the job. Empty when the result carries no provenance.
 */
export function describeProvenance(p: Provenance | null | undefined): string[] {
	if (!p) return [];
	const stages = p.stages ?? [];
	const worker = p.worker_id ? `Worker ${p.worker_id}` : "";
	if (stages.length === 0) return worker ? [worker] : [];
	return stages.map((s, i) => {
		const model = s.model && s.model !== s.engine ? `${s.engine} · ${s.model}` : s.engine;
		const where = s.accelerator && s.accelerator !== "auto" ? ` (${s.accelerator})` : "";
		const parts = [i === 0 ? worker : "", `${s.stage}: ${model}${where}`, formatSeconds(s.seconds)];
		return parts.filter(part => part.length > 0).join(" · ");
	});
}
