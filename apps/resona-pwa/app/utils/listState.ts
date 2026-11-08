/**
 * `useState` keys of the list views (Aufnahmen, Dokumente). The rows, the search
 * text and the date filter outlive the list component, so coming back from a
 * detail page shows the same list immediately (the browser can restore the scroll
 * position into it) and with the same filters while a refresh runs in the
 * background. Cleared on logout so another user never sees them.
 */
export const LIST_STATE_KEYS = [
	"recordings-items",
	"recordings-search",
	"recordings-since",
	"documents-items",
	"documents-search",
] as const;
