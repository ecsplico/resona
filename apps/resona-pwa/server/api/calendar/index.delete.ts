import { handleDisconnect, providerFromQuery } from "~~/server/utils/calendar/handlers";

// DELETE /api/calendar?provider=… → disconnect + revoke.
export default defineEventHandler(event => handleDisconnect(event, providerFromQuery(event)));
