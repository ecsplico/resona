/**
 * Recommendation badges for the hub's engine catalogue (`GET /v1/engines`).
 *
 * The hub attaches `recommendation` to an audio candidate the organization has measured
 * (promoted baseline or testbench): one of the roles `recommended`, `fast`, `accurate`.
 * The UI shows them as `empfohlen`, `schnell`, `genau`.
 */

export type RecommendationRole = "recommended" | "fast" | "accurate";

export interface EngineRecommendation {
	role: RecommendationRole;
	error_percent: number;
	seconds_per_30s: number;
	basis: "baseline" | "testbench";
}

export const ROLE_LABELS: Record<RecommendationRole, string> = {
	recommended: "empfohlen",
	fast: "schnell",
	accurate: "genau",
};

const ROLE_ORDER: RecommendationRole[] = ["recommended", "fast", "accurate"];

interface WithCandidates {
	candidates: Array<{ recommendation?: EngineRecommendation | null }>;
}

/** The engine's roles across its candidates, in role order, each once. */
export function engineRoles(engine: WithCandidates): RecommendationRole[] {
	const roles = new Set(
		engine.candidates.map(c => c.recommendation?.role).filter((r): r is RecommendationRole => !!r),
	);
	return ROLE_ORDER.filter(r => roles.has(r));
}

/** German badge text for an engine, e.g. `empfohlen, schnell`; empty when unmeasured. */
export function engineBadge(engine: WithCandidates): string {
	return engineRoles(engine).map(r => ROLE_LABELS[r]).join(", ");
}
