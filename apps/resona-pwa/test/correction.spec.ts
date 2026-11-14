import { describe, it, expect } from "vitest";
import { diffEdits, rebaseInsertion, textSplice } from "~/utils/correction";

describe("diffEdits", () => {
	it("reports nothing for an unchanged text", () => {
		expect(diffEdits("Hallo Welt", "Hallo Welt")).toEqual([]);
	});

	it("reports separate word changes as separate edits", () => {
		expect(diffEdits("der Patient hat Fieber seit gestern", "die Patientin hat Fieber seit heute"))
			.toEqual([
				{ before: "der Patient", after: "die Patientin" },
				{ before: "gestern", after: "heute" },
			]);
	});

	it("reports an oversized changed middle as one edit", () => {
		const a = Array.from({ length: 2100 }, (_, i) => `a${i}`).join(" ");
		const b = Array.from({ length: 2100 }, (_, i) => `b${i}`).join(" ");
		expect(diffEdits(`Anfang ${a} Ende`, `Anfang ${b} Ende`)).toEqual([{ before: a, after: b }]);
	});

	it("reports nothing for a paragraph that was only moved", () => {
		expect(diffEdits("erster Absatz\nzweiter Absatz\ndritter Absatz", "zweiter Absatz\ndritter Absatz\nerster Absatz"))
			.toEqual([]);
		expect(diffEdits("A eins\nB zwei\nC drei", "B zwei\nA eins\nC drei")).toEqual([]);
	});

	it("reports only the words changed inside a moved paragraph", () => {
		expect(diffEdits(
			"der Patient hat Fieber seit gestern\nzweiter Absatz\ndritter Absatz",
			"zweiter Absatz\ndritter Absatz\nder Patient hat Fieber seit heute",
		)).toEqual([{ before: "gestern", after: "heute" }]);
	});

	it("still reports a paragraph replaced by different text in its place", () => {
		expect(diffEdits("eins\nalt und falsch\ndrei", "eins\nganz neu geschrieben\ndrei"))
			.toEqual([{ before: "alt und falsch", after: "ganz neu geschrieben" }]);
	});
});

describe("textSplice", () => {
	it("finds the one changed span", () => {
		expect(textSplice("Hallo […] Welt", "Hallo diktiert  Welt"))
			.toEqual({ at: 6, removed: "[…]", inserted: "diktiert " });
	});
});

describe("rebaseInsertion", () => {
	it("puts the insertion onto the saved text before the unsaved typing", () => {
		// Editor: "Neu Hallo Welt" (typed "Neu "), dictated "X " at the end.
		expect(rebaseInsertion("Hallo Welt", "Neu Hallo Welt", 14, "X ")).toBe("Hallo WeltX ");
	});

	it("puts the insertion onto the saved text after the unsaved typing", () => {
		// Editor: "Hallo Welt, geändert", dictated "X " at the start.
		expect(rebaseInsertion("Hallo Welt", "Hallo Welt, geändert", 0, "X ")).toBe("X Hallo Welt");
	});

	it("gives up when the insertion sits inside the unsaved typing", () => {
		// Editor: "Hallo liebe schöne Welt", typed "liebe schöne ", dictated inside it.
		expect(rebaseInsertion("Hallo Welt", "Hallo liebe schöne Welt", 12, "X ")).toBeNull();
	});

	it("inserts at the same place when nothing else changed", () => {
		expect(rebaseInsertion("Hallo Welt", "Hallo Welt", 6, "X ")).toBe("Hallo X Welt");
	});
});
