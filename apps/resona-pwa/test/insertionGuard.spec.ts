import { describe, it, expect } from "vitest";
import {
	droppedInsertionMessage,
	mapPosition,
	resolveInsertion,
	snapshotInsertion,
	spaceInsertion,
	type InsertionTarget,
} from "~/utils/insertionGuard";
import { rebaseOffset } from "~/utils/correction";

function draft(overrides: Partial<InsertionTarget> = {}): InsertionTarget {
	return { ownerKey: "recording-1", text: "", selection: { start: 0, end: 0 }, ...overrides };
}

function caret(text: string, at = text.length): InsertionTarget {
	return draft({ text, selection: { start: at, end: at } });
}

// Cases ported from T3 Code's controller.test.ts (`resolveTranscriptCommit`).
describe("resolveInsertion", () => {
	it.each([
		["Hello", "world", "Hello world"],
		["Hello", "世界", "Hello世界"],
		["Hello", "𝟚", "Hello 𝟚"],
		["你好", "世界", "你好世界"],
	])("preserves script boundaries with an autodetected language: %s + %s", (text, spoken, expected) => {
		const captured = snapshotInsertion(caret(text));
		expect(resolveInsertion(captured, captured, spoken, { language: "auto" })).toMatchObject({ kind: "insert", text: expected });
	});

	it.each([
		["Korean", "코드를", 3, "검토해 주세요", "ko_KR", "코드를 검토해 주세요"],
		["a supplementary letter", "𞤀", 2, "𞤦", "ff-Adlm", "𞤀 𞤦"],
		["a trailing combining mark", "नमस्ते", 6, "दुनिया", "hi-IN", "नमस्ते दुनिया"],
		["a non-ASCII word after the caret", "Änderungen", 0, "Weitere", "de_DE", "Weitere Änderungen"],
		["spaceless Japanese", "修正キャッシュ", 7, "テストも", "ja-JP", "修正キャッシュテストも"],
		["spaceless Thai", "โลก", 0, "สวัสดี", "th-TH", "สวัสดีโลก"],
	])("inserts at a caret with the language's word spacing: %s", (_case, text, at, spoken, language, expected) => {
		const captured = snapshotInsertion(caret(text, at));
		expect(resolveInsertion(captured, captured, spoken, { language })).toMatchObject({ kind: "insert", text: expected });
	});

	it("replaces the recorded UTF-16 selection around emoji and tokens", () => {
		const text = "Fix 🧪 then $review please";
		const tokenStart = text.indexOf("$review");
		const captured = snapshotInsertion(draft({ text, selection: { start: tokenStart, end: tokenStart + "$review".length } }));
		expect(resolveInsertion(captured, captured, "use the mobile skill", { language: "en-US" })).toEqual({
			kind: "insert",
			text: "Fix 🧪 then use the mobile skill please",
			selection: { start: tokenStart + 20, end: tokenStart + 20 },
		});
	});

	it.each([
		["at the start of the selection", { start: 6, end: 11 }, "hello [cite]world", "hello [cite]world new text"],
		["inside the selection", { start: 6, end: 11 }, "hello wo[cite]rld", "hello wo[cite]rld new text"],
		["at the caret", { start: 6, end: 6 }, "hello [cite] world", "hello [cite] new text world"],
		["that repeats the selection", { start: 0, end: 5 }, "hello there hello world", "hello there hello new text world"],
		["that repeats the selection next to it", { start: 0, end: 5 }, "hello hello world", "hello hello new text world"],
	])("keeps text inserted %s meanwhile", (_case, selection, current, expected) => {
		const captured = snapshotInsertion(draft({ text: "hello world", selection }));
		const result = resolveInsertion(captured, draft({ text: current }), "new text", { language: "en-US" });
		expect(result).toMatchObject({ kind: "insert", text: expected });
	});

	it("keeps every citation when several were inserted meanwhile", () => {
		const captured = snapshotInsertion(draft({ text: "[todo", selection: { start: 0, end: 1 } }));
		const current = "[A](t3-citation://v1/a)[todo[B](t3-citation://v1/b)";
		expect(resolveInsertion(captured, draft({ text: current }), "spoken", { language: "en-US" }))
			.toMatchObject({ kind: "insert", text: `${current} spoken` });
	});

	it("keeps surrogate pairs whole when an emoji changed meanwhile", () => {
		const captured = snapshotInsertion(caret("🔔", 0));
		expect(resolveInsertion(captured, caret("🤔", 0), "hello", { language: "en-US" }))
			.toMatchObject({ kind: "insert", text: "🤔hello" });
	});

	it("does not insert into a missing target or another owner's", () => {
		const captured = snapshotInsertion(draft());
		expect(resolveInsertion(captured, null, "text")).toEqual({ kind: "dropped", reason: "owner-changed" });
		expect(resolveInsertion(captured, draft({ ownerKey: "other" }), "text")).toEqual({ kind: "dropped", reason: "owner-changed" });
	});

	// Resona additions.

	it("drops a result whose place lies inside text the user replaced", () => {
		const captured = snapshotInsertion(caret("hello world", 8));
		expect(resolveInsertion(captured, caret("hello earth"), "new"))
			.toEqual({ kind: "dropped", reason: "draft-changed-unmappable" });
	});

	it("moves the place along with typing before it", () => {
		const captured = snapshotInsertion(caret("Befund unauffällig.", 6));
		expect(resolveInsertion(captured, caret("Kurz: Befund unauffällig."), "heute")).toEqual({
			kind: "insert",
			text: "Kurz: Befund heute unauffällig.",
			selection: { start: 18, end: 18 },
		});
	});

	it("keeps its place when the user typed after it", () => {
		const captured = snapshotInsertion(caret("Befund unauffällig.", 6));
		expect(resolveInsertion(captured, caret("Befund unauffällig. Ende"), "heute"))
			.toMatchObject({ kind: "insert", text: "Befund heute unauffällig. Ende" });
	});

	it("drops when edits on both sides leave no shared text around the place", () => {
		// One changed span covers both edits, so the place lies inside it.
		const captured = snapshotInsertion(caret("Befund unauffällig.", 6));
		expect(resolveInsertion(captured, caret("Kurz: Befund unauffällig. Ende"), "heute"))
			.toEqual({ kind: "dropped", reason: "draft-changed-unmappable" });
	});

	it("puts no space before punctuation and one between words (German default)", () => {
		expect(resolveInsertion(snapshotInsertion(caret("Der Befund.", 10)), caret("Der Befund.", 10), "ist gut"))
			.toMatchObject({ text: "Der Befund ist gut." });
		expect(resolveInsertion(snapshotInsertion(caret("(", 1)), caret("(", 1), "siehe oben"))
			.toMatchObject({ text: "(siehe oben" });
	});

	it("leaves the text unchanged for an empty result", () => {
		const captured = snapshotInsertion(caret("Hallo", 2));
		expect(resolveInsertion(captured, caret("Hallo", 2), "   ")).toEqual({ kind: "insert", text: "Hallo", selection: { start: 2, end: 2 } });
	});

	it("with onChange drop: replaces an unchanged whole-field selection, drops on any change", () => {
		const field = (text: string, revision = 0) => draft({ ownerKey: "live-1", text, selection: { start: 0, end: text.length }, revision });
		const captured = snapshotInsertion(field("Diktat 01.02.2026"));
		expect(resolveInsertion(captured, field("Diktat 01.02.2026"), "Kopfschmerzen seit drei Tagen", { onChange: "drop" }))
			.toEqual({ kind: "insert", text: "Kopfschmerzen seit drei Tagen", selection: { start: 29, end: 29 } });
		expect(resolveInsertion(captured, field("Diktat 01.02.2026 Knie"), "Titel", { onChange: "drop" }))
			.toEqual({ kind: "dropped", reason: "draft-changed-unmappable" });
		// Typed back to the same text: a new revision still counts as the user's.
		expect(resolveInsertion(captured, field("Diktat 01.02.2026", 1), "Titel", { onChange: "drop" }))
			.toEqual({ kind: "dropped", reason: "draft-changed-unmappable" });
		expect(resolveInsertion(captured, { ...field("Diktat 01.02.2026"), ownerKey: "live-2" }, "Titel", { onChange: "drop" }))
			.toEqual({ kind: "dropped", reason: "owner-changed" });
	});

	it("a snapshot is a copy: later edits of the target object do not reach it", () => {
		const target = caret("Hallo");
		const captured = snapshotInsertion(target);
		target.text = "Hallo Welt";
		target.selection.start = 0;
		expect(captured.text).toBe("Hallo");
		expect(captured.selection).toEqual({ start: 5, end: 5 });
	});
});

describe("mapPosition", () => {
	it("keeps positions before the edit and shifts those after it", () => {
		expect(mapPosition("Hallo Welt", "Hallo schöne Welt", 2)).toBe(2);
		expect(mapPosition("Hallo Welt", "Hallo schöne Welt", 8)).toBe(15);
	});

	it("puts a position at an insertion behind it by default, in front of it with bias before", () => {
		expect(mapPosition("Hallo Welt", "Hallo schöne Welt", 6)).toBe(13);
		expect(mapPosition("Hallo Welt", "Hallo schöne Welt", 6, { bias: "before" })).toBe(6);
	});

	it("has no place inside replaced text", () => {
		expect(mapPosition("Hallo liebe Welt", "Hallo Welt", 8)).toBeNull();
	});

	it("aligns repeated text from either side", () => {
		expect(mapPosition("hello world", "hello hello world", 5, { alignment: "prefix" })).toBe(5);
		expect(mapPosition("hello world", "hello hello world", 5, { alignment: "suffix" })).toBe(11);
	});
});

describe("spaceInsertion", () => {
	it("spaces words and leaves punctuation and brackets tight", () => {
		expect(spaceInsertion("Hallo", "", "Welt")).toBe(" Welt");
		expect(spaceInsertion("", "Welt", "Hallo")).toBe("Hallo ");
		expect(spaceInsertion("Hallo ", "Welt", "liebe")).toBe("liebe ");
		expect(spaceInsertion("Hallo", ".", "Welt")).toBe(" Welt");
		expect(spaceInsertion("(", ")", "Klammer")).toBe("Klammer");
	});
});

// Re-dictation anchors are carried onto the saved text through the same mapping.
describe("rebaseOffset (redictation anchors)", () => {
	it("keeps an anchor in front of unsaved typing that starts at it", () => {
		expect(rebaseOffset("Hallo Welt", "Hallo neue Welt", 6)).toBe(6);
	});

	it("has no anchor inside unsaved typing", () => {
		expect(rebaseOffset("Hallo Welt", "Hallo neue Welt", 8)).toBeNull();
	});
});

describe("droppedInsertionMessage", () => {
	it("words both reasons in German", () => {
		expect(droppedInsertionMessage("Der Titelvorschlag", "owner-changed")).toBe("Der Titelvorschlag kam zu spät und wurde verworfen.");
		expect(droppedInsertionMessage("Der Titelvorschlag", "draft-changed-unmappable"))
			.toBe("Der Titelvorschlag wurde verworfen, weil sich der Text inzwischen geändert hat.");
	});
});
