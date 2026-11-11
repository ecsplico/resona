// Create ownership guard for the `user` policy (#19).
//
// Directus 11 ignores item rules (`permissions`) on create and checks `validation`
// against the request payload, where an M2O is a bare id: a relational validation
// (`recording.user_created = $CURRENT_USER`) rejected every create, an item rule let a
// user attach rows to someone else's recording. This hook runs inside the create
// transaction instead: every parent named in the payload must be readable with the
// caller's own permissions, which the `user` policy limits to the user's own rows.
// Someone else's or a missing parent → 403, no parent id → 400; a batch fails as a
// whole. Admins (the project-worker's static token) and internal calls pass.
//
// Plain ESM, no build step: Directus loads `index.js` from /directus/extensions
// (copied in by directus/Dockerfile.directus). Bootstrap refuses to drop the old
// create rules unless this extension is loaded.

/** collection → parents: M2O field, related collection, extra filter on the parent */
const GUARDS = {
	transcripts: [{ field: "recording", collection: "recordings" }],
	transcript_corrections: [
		{ field: "recording", collection: "recordings" },
		// the corrected transcript belongs to that recording
		{ field: "transcript", collection: "transcripts", filter: payload => ({ recording: { _eq: parentId(payload.recording) } }) },
	],
};

/** A bare id, or `{ id }` (an existing item); a nested create has no id. */
function parentId(value) {
	if (typeof value === "string" && value) return value;
	if (value && typeof value === "object" && typeof value.id === "string" && value.id) return value.id;
	return null;
}

// Directus' error handler renders any error named "DirectusError" with its
// status/code/extensions; @directus/errors itself is not resolvable from here.
class InvalidPayloadError extends Error {
	constructor(reason) {
		super(`Invalid payload. ${reason}.`);
		this.name = "DirectusError";
		this.status = 400;
		this.code = "INVALID_PAYLOAD";
		this.extensions = { reason };
	}
}

export default ({ filter }, { services }) => {
	for (const [collection, parents] of Object.entries(GUARDS)) {
		filter(`${collection}.items.create`, async (payload, _meta, { accountability, schema, database }) => {
			if (!accountability || accountability.admin) return payload;
			for (const parent of parents) {
				const id = parentId(payload[parent.field]);
				if (!id) throw new InvalidPayloadError(`"${parent.field}" must reference an existing item`);
				const items = new services.ItemsService(parent.collection, { schema, accountability, knex: database });
				// throws ForbiddenError (403) when the caller cannot read it
				await items.readOne(id, parent.filter ? { filter: parent.filter(payload) } : {});
			}
			return payload;
		});
	}
};
