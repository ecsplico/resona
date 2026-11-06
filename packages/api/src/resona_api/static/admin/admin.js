import { registerIconLibrary } from "/admin/vendor/webawesome/webawesome.loader.js";

// Icons resolve to the hub's own SVGs: the Font Awesome CDN is never contacted, and the
// built-in `system` icons (data: URLs loaded with fetch, which `connect-src 'self'` blocks)
// are swapped for local equivalents.
const iconUrl = (name) => "/admin/icons/" + name + ".svg";
const SYSTEM_ICONS = {
  check: "check",
  "chevron-down": "chevron-down",
  "chevron-left": "chevron-left",
  "chevron-right": "chevron-right",
  "circle-xmark": "circle-x",
  copy: "copy",
  eye: "eye",
  "eye-slash": "eye-off",
  xmark: "x",
};
registerIconLibrary("default", {
  resolver: (name) => (/^[a-z0-9-]+$/.test(name) ? iconUrl(name) : ""),
});
registerIconLibrary("system", {
  resolver: (name) => iconUrl(SYSTEM_ICONS[name] || "help-circle"),
});

const TOKEN_KEY = "resona-admin-token";
const ADMIN_API = "/v1/admin";
const DAY_MS = 24 * 60 * 60 * 1000;
const ROLE_LABELS = {
  org_admin: "Organization admin",
  hub_operator: "Hub operator",
  client: "Client",
  worker: "Worker",
};
const QUEUE_REASONS = {
  no_capable_worker: "No capable worker online",
  release_not_approved: "Release not approved",
  waiting_capacity: "Waiting for capacity",
};
const TABS = ["workers", "credentials", "jobs", "settings", "testbench"];

const state = {
  token: null,
  me: null,
  org: null,
  tab: "workers",
  jobsCursor: null,
  // Jobs currently listed, by id; feeds the pending count in the summary strip.
  jobs: new Map(),
  // Bumped whenever the view context changes; responses for an older context are dropped.
  seq: 0,
  // Bumped whenever the selected organization changes; guards the summary strip.
  orgSeq: 0,
  // Latest load sequence per tab; the spinner stops only for the newest load.
  loading: {},
};

const $ = (id) => document.getElementById(id);

// ── Token storage ──────────────────────────────────────────────────────────

function loadToken() {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch (_) {
    return null;
  }
}

function saveToken(token) {
  try {
    window.sessionStorage.setItem(TOKEN_KEY, token);
  } catch (_) {
    // Storage unavailable: the token lives only in memory for this page.
  }
}

function clearToken() {
  try {
    window.sessionStorage.removeItem(TOKEN_KEY);
  } catch (_) {
    // Nothing stored.
  }
}

// ── API ────────────────────────────────────────────────────────────────────

class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
    this.handled = false;
  }
}

function toApiError(status, statusText, data) {
  const body = data && typeof data === "object" ? data : {};
  if (body.message) {
    return new ApiError(status, body.error ? String(body.error) : "error", String(body.message));
  }
  const detail = body.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && detail.message) {
    return new ApiError(status, detail.error ? String(detail.error) : "error", String(detail.message));
  }
  if (Array.isArray(detail)) {
    const message = detail
      .map((item) => {
        const loc = item && Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
        const msg = item && item.msg !== undefined ? String(item.msg) : "invalid value";
        return loc ? loc + ": " + msg : msg;
      })
      .join("; ");
    return new ApiError(status, "validation_error", message || "Invalid request.");
  }
  if (typeof detail === "string" && detail) {
    return new ApiError(status, "error", detail);
  }
  const reason = statusText ? "HTTP " + status + " " + statusText : "HTTP " + status;
  return new ApiError(status, body.error ? String(body.error) : "error", "Request failed (" + reason + ").");
}

// One request with the session token. Shared by the JSON, binary and multipart helpers
// so the network error, 401 sign-out and ApiError handling stay in one place. A non-2xx
// response is read as JSON (when it parses) and thrown; a 2xx response is returned
// unread. `json` sends a JSON body; `form` sends FormData (the browser sets the
// multipart Content-Type and boundary); `accept` defaults to JSON.
async function request(method, path, options) {
  const opts = options || {};
  const headers = {
    Authorization: "Bearer " + state.token,
    Accept: opts.accept || "application/json",
  };
  const init = { method: method, headers: headers, cache: "no-store", credentials: "omit" };
  if (opts.headers) {
    Object.assign(headers, opts.headers);
  }
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
  } else if (opts.form) {
    init.body = opts.form;
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (_) {
    throw new ApiError(0, "network_error", "Could not reach the hub.");
  }
  if (!response.ok) {
    const error = toApiError(response.status, response.statusText, await parseJson(response));
    if (response.status === 401) {
      signOut("Token invalid or revoked");
      error.handled = true;
    }
    throw error;
  }
  return response;
}

async function parseJson(response) {
  const text = response.status === 204 ? "" : await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch (_) {
    return null;
  }
}

async function api(method, path, body) {
  return parseJson(await request(method, path, { json: body }));
}

// Binary download (an item's original): the response body as a Blob of its Content-Type.
async function apiBlob(path) {
  const response = await request("GET", path, { accept: "*/*" });
  return response.blob();
}

// Multipart upload: `form` is a FormData; the Content-Type header is left to the browser.
async function apiUpload(path, form) {
  return parseJson(await request("POST", path, { form: form }));
}

// GET that also returns the ETag header (the settings revision for a later If-Match).
async function apiWithEtag(path) {
  const response = await request("GET", path);
  return { data: await parseJson(response), etag: response.headers.get("ETag") };
}

function orgPath() {
  return ADMIN_API + "/organizations/" + encodeURIComponent(state.org);
}

// ── Error banner ───────────────────────────────────────────────────────────

function showError(message) {
  // Before sign-in completes the error belongs on the sign-in slip.
  if ($("app").hidden) {
    const note = $("login-message");
    note.textContent = message;
    note.hidden = false;
    return;
  }
  $("banner-text").textContent = message;
  $("banner").hidden = false;
}

function hideBanner() {
  $("banner").hidden = true;
  $("banner-text").textContent = "";
}

function report(error) {
  if (error && error.handled) {
    return;
  }
  showError(error && error.message ? error.message : String(error));
}

// ── DOM helpers ────────────────────────────────────────────────────────────

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = String(text);
  }
  return node;
}

// Creates a (Web Awesome) element from attributes; `true` sets a boolean attribute.
function wa(tag, attrs, text) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([name, value]) => {
    if (value === true) {
      node.setAttribute(name, "");
    } else if (value !== false && value !== undefined && value !== null) {
      node.setAttribute(name, String(value));
    }
  });
  if (text !== undefined && text !== null) {
    node.textContent = String(text);
  }
  return node;
}

function isBlank(value) {
  return value === undefined || value === null || value === "";
}

function dash() {
  return el("span", "quiet", "—");
}

// A table cell labelled for the stacked phone layout.
function cell(label, content, className) {
  const td = el("td", className);
  td.dataset.label = label;
  if (content instanceof Node) {
    td.appendChild(content);
  } else if (isBlank(content)) {
    td.appendChild(dash());
  } else {
    td.textContent = String(content);
  }
  return td;
}

function stack(...nodes) {
  const wrap = el("div", "stack");
  nodes.filter(Boolean).forEach((node) => wrap.appendChild(node));
  return wrap;
}

// Machine identifiers read as grouped tracking numbers (`wrk · f21e · 64ec …`) while
// the element's text stays the raw id, so selecting and copying yields it unchanged.
function trackingNumber(raw) {
  const text = String(raw);
  const node = el("span", "trk");
  const match = /^([a-z]+)_([0-9a-f]{8,})$/i.exec(text);
  if (!match) {
    node.classList.add("trk-plain");
    node.textContent = text;
    return node;
  }
  node.appendChild(el("span", "trk-prefix", match[1]));
  node.appendChild(el("span", "trk-joint", "_"));
  for (let index = 0; index < match[2].length; index += 4) {
    // <wbr> adds a line-break opportunity between groups without adding text.
    node.appendChild(document.createElement("wbr"));
    node.appendChild(el("span", "trk-group", match[2].slice(index, index + 4)));
  }
  return node;
}

function idWithCopy(raw, copyLabel) {
  if (isBlank(raw)) {
    return dash();
  }
  const wrap = el("span", "id-copy");
  wrap.appendChild(trackingNumber(raw));
  wrap.appendChild(
    wa("wa-copy-button", {
      value: String(raw),
      "copy-label": copyLabel,
      "success-label": "Copied",
      "error-label": "Copy failed",
    })
  );
  return wrap;
}

function mono(text) {
  return isBlank(text) ? dash() : el("span", "machine", text);
}

function numeral(value) {
  return isBlank(value) ? dash() : el("span", "num", value);
}

function absoluteTime(iso) {
  return wa("wa-format-date", {
    date: iso,
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function relativeTime(iso) {
  return wa("wa-relative-time", { date: iso, sync: true });
}

// Absolute timestamp with its distance from now underneath.
function timeNode(iso) {
  if (!iso) {
    return dash();
  }
  const wrap = el("span", "when");
  wrap.title = iso;
  wrap.appendChild(absoluteTime(iso));
  const rel = el("span", "when-rel");
  rel.appendChild(relativeTime(iso));
  wrap.appendChild(rel);
  return wrap;
}

function badge(text, variant, appearance) {
  return wa("wa-badge", { variant: variant, appearance: appearance || "filled-outlined" }, text);
}

// Red double-ruled rubber stamp: revoked tokens and workers, cancelled jobs.
function stamp(text) {
  return el("span", "stamp", text);
}

function actionButton(label, options) {
  const node = wa("wa-button", {
    size: "xs",
    variant: options.variant || "neutral",
    appearance: options.appearance || "outlined",
    id: options.id,
  });
  if (options.icon) {
    node.appendChild(wa("wa-icon", { slot: "start", name: options.icon }));
  }
  node.appendChild(document.createTextNode(label));
  node.addEventListener("click", options.onClick);
  return node;
}

function tooltipFor(target, text) {
  return wa("wa-tooltip", { for: target.id }, text);
}

function formatCandidate(candidate) {
  if (!candidate || typeof candidate !== "object") {
    return candidate === undefined || candidate === null ? "" : String(candidate);
  }
  let text = String(candidate.engine) + " / " + String(candidate.model);
  if (candidate.model_revision) {
    text += "@" + String(candidate.model_revision);
  }
  if (candidate.accelerator) {
    text += " · " + String(candidate.accelerator);
  }
  if (candidate.ocr_engine) {
    text += " · ocr " + String(candidate.ocr_engine);
  }
  return text;
}

// One tag per capability; colour carries the state: green = approved and reported,
// outlined = reported but not approved, amber = approved but no longer reported.
const CAPABILITY_STATES = {
  approved: { variant: "success", appearance: "filled-outlined", title: "Approved" },
  reported: { variant: "neutral", appearance: "outlined", title: "Reported, not approved" },
  stale: { variant: "warning", appearance: "filled-outlined", title: "Approved, no longer reported" },
};

function capabilityKey(candidate) {
  return JSON.stringify(
    ["engine", "model", "model_revision", "accelerator", "ocr_engine"].map((field) =>
      candidate && typeof candidate === "object" ? candidate[field] ?? null : candidate
    )
  );
}

function capabilityTags(reported, approved) {
  const approvedKeys = new Set(approved.map(capabilityKey));
  const reportedKeys = new Set(reported.map(capabilityKey));
  const entries = reported
    .map((candidate) => [candidate, approvedKeys.has(capabilityKey(candidate)) ? "approved" : "reported"])
    .concat(approved.filter((candidate) => !reportedKeys.has(capabilityKey(candidate))).map((c) => [c, "stale"]));
  const tags = el("div", "cap-tags");
  entries.forEach(([candidate, kind]) => {
    const state = CAPABILITY_STATES[kind];
    const tag = wa("wa-tag", { size: "s", variant: state.variant, appearance: state.appearance }, formatCandidate(candidate));
    tag.classList.add("cap-tag", "cap-" + kind);
    tag.title = state.title;
    tag.setAttribute("aria-label", formatCandidate(candidate) + " (" + state.title.toLowerCase() + ")");
    tags.appendChild(tag);
  });
  return entries.length ? tags : "";
}

// Shows or hides a table and its empty note together.
function setEmpty(name) {
  const empty = $(name + "-body").children.length === 0;
  $(name + "-wrap").hidden = empty;
  $(name + "-empty").hidden = !empty;
}

function skeletonRows(body) {
  const columns = Number(body.dataset.columns) || 1;
  for (let index = 0; index < 3; index += 1) {
    const row = el("tr", "skeleton-row");
    const td = el("td");
    td.colSpan = columns;
    td.appendChild(wa("wa-skeleton", { effect: "sheen" }));
    row.appendChild(td);
    body.appendChild(row);
  }
}

function setLoading(tab, seq, on) {
  if (!on && state.loading[tab] !== seq) {
    return;
  }
  state.loading[tab] = on ? seq : null;
  const panel = $("panel-" + tab);
  panel.querySelector("[data-spinner]").hidden = !on;
  panel.querySelectorAll("tbody").forEach((body) => {
    body.setAttribute("aria-busy", on ? "true" : "false");
    if (on && body.children.length === 0) {
      body.closest(".table-wrap").hidden = false;
      skeletonRows(body);
    }
    if (!on) {
      body.querySelectorAll(".skeleton-row").forEach((row) => row.remove());
    }
  });
}

// Runs an action triggered by a button: marks it busy while pending and reports errors.
async function run(trigger, action) {
  if (trigger) {
    trigger.disabled = true;
    if (trigger.localName === "wa-button") {
      trigger.loading = true;
    }
  }
  hideBanner();
  try {
    await action();
  } catch (error) {
    report(error);
  } finally {
    if (trigger && trigger.isConnected) {
      trigger.disabled = false;
      if (trigger.localName === "wa-button") {
        trigger.loading = false;
      }
    }
  }
}

// Runs `callback` once the component is defined and has rendered its shadow DOM, so
// methods like focus() find their inner control.
function whenReady(node, callback) {
  customElements
    .whenDefined(node.localName)
    .then(() => node.updateComplete)
    .then(() => callback(node));
}

// ── Confirmation dialog ────────────────────────────────────────────────────

let settleConfirmation = null;

function confirmAction(options) {
  const dialog = $("confirm");
  if (settleConfirmation) {
    settleConfirmation(false);
  }
  dialog.label = options.title;
  $("confirm-text").textContent = options.message;
  $("confirm-ok").textContent = options.confirmLabel;
  $("confirm-cancel").textContent = options.dismissLabel || "Go back";
  dialog.open = true;
  return new Promise((resolve) => {
    settleConfirmation = (value) => {
      settleConfirmation = null;
      resolve(value);
    };
  });
}

function closeConfirmation(value) {
  if (settleConfirmation) {
    settleConfirmation(value);
  }
  $("confirm").open = false;
}

// ── Summary strip ──────────────────────────────────────────────────────────

function setTally(id, text) {
  $(id).textContent = text;
}

function resetTally() {
  ["tally-online", "tally-workers", "tally-tokens", "tally-pending"].forEach((id) => setTally(id, "–"));
}

function tallyWorkers(items) {
  const active = items.filter((worker) => !worker.revoked);
  setTally("tally-online", String(active.filter((worker) => worker.online).length));
  setTally("tally-workers", String(active.length));
}

function tallyCredentials(items) {
  setTally("tally-tokens", String(items.filter((credential) => !credential.revoked_at).length));
}

function tallyJobs() {
  let pending = 0;
  state.jobs.forEach((job) => {
    if (job.status === "pending") {
      pending += 1;
    }
  });
  // With more pages unread the count is a lower bound.
  setTally("tally-pending", String(pending) + (state.jobsCursor ? "+" : ""));
}

async function loadSummary() {
  const orgSeq = state.orgSeq;
  const [workers, credentials, jobs] = await Promise.allSettled([
    api("GET", orgPath() + "/workers"),
    api("GET", orgPath() + "/credentials"),
    api("GET", orgPath() + "/jobs"),
  ]);
  if (orgSeq !== state.orgSeq) {
    return;
  }
  if (workers.status === "fulfilled") {
    tallyWorkers((workers.value && workers.value.items) || []);
  }
  if (credentials.status === "fulfilled") {
    tallyCredentials((credentials.value && credentials.value.items) || []);
  }
  // The jobs tab owns the pending count once it has loaded a page.
  if (jobs.status === "fulfilled" && state.jobs.size === 0) {
    const items = (jobs.value && jobs.value.items) || [];
    const pending = items.filter((job) => job.status === "pending").length;
    setTally("tally-pending", String(pending) + (jobs.value && jobs.value.next_cursor ? "+" : ""));
  }
}

// ── Session ────────────────────────────────────────────────────────────────

function resetViews() {
  hideIssued();
  ["workers", "credentials", "jobs", "baselines", "releases", "items", "runs", "lb-audio", "lb-document"].forEach((name) => {
    $(name + "-body").replaceChildren();
    $(name + "-empty").hidden = true;
    $(name + "-wrap").hidden = false;
  });
  $("settings-revision").textContent = "";
  $("settings-json").textContent = "";
  $("jobs-more").hidden = true;
  state.jobsCursor = null;
  state.jobs.clear();
  resetTestbench();
  resetTally();
}

function setMenu(open) {
  $("rail").classList.toggle("is-open", open);
  $("menu-toggle").setAttribute("aria-expanded", open ? "true" : "false");
}

function showLogin(message) {
  $("app").hidden = true;
  $("login").hidden = false;
  const note = $("login-message");
  note.textContent = message || "";
  note.hidden = !message;
  whenReady($("login-token"), (input) => input.focus());
}

function signOut(message) {
  state.seq += 1;
  state.orgSeq += 1;
  state.token = null;
  state.me = null;
  state.org = null;
  clearToken();
  closeConfirmation(false);
  resetViews();
  $("org-list").replaceChildren();
  $("org-select").replaceChildren();
  $("org-title").textContent = "";
  $("new-org-form").hidden = true;
  setMenu(false);
  hideBanner();
  showLogin(message);
}

async function enter() {
  let me;
  try {
    me = await api("GET", ADMIN_API + "/me");
  } catch (error) {
    if (error.handled) {
      return;
    }
    if (error.status === 403) {
      signOut("This token is not an organization admin or hub operator token.");
      return;
    }
    showLogin();
    report(error);
    return;
  }
  state.me = me;
  $("me-role").textContent = ROLE_LABELS[me.role] || me.role;
  $("me-org").textContent = me.organization_id;
  $("login").hidden = true;
  $("login-message").hidden = true;
  $("app").hidden = false;
  $("new-org-form").hidden = me.role !== "hub_operator";
  try {
    await loadOrganizations(null);
  } catch (error) {
    report(error);
  }
}

// ── Organizations ──────────────────────────────────────────────────────────

async function loadOrganizations(preferred) {
  const data = await api("GET", ADMIN_API + "/organizations");
  const items = (data && data.items) || [];
  const ids = items.map((item) => item.id);
  const home = state.me.organization_id;

  const list = $("org-list");
  const select = $("org-select");
  list.replaceChildren();
  select.replaceChildren();
  items.forEach((item) => {
    const entry = el("li");
    const choose = el("button", "org-item");
    choose.type = "button";
    choose.dataset.org = item.id;
    choose.appendChild(el("span", "org-name", item.id));
    if (item.id === home) {
      choose.appendChild(el("span", "org-home caps", "Home"));
    }
    choose.addEventListener("click", () => chooseOrganization(item.id));
    entry.appendChild(choose);
    list.appendChild(entry);

    const option = wa("wa-option", { value: item.id }, item.id === home ? item.id + " (yours)" : item.id);
    select.appendChild(option);
  });

  let chosen = null;
  for (const candidate of [preferred, state.org, home]) {
    if (candidate && ids.includes(candidate)) {
      chosen = candidate;
      break;
    }
  }
  if (!chosen && ids.length) {
    chosen = ids[0];
  }
  select.disabled = ids.length <= 1;
  selectOrganization(chosen);
}

function chooseOrganization(orgId) {
  setMenu(false);
  if (orgId === state.org) {
    loadTab();
    return;
  }
  selectOrganization(orgId);
}

function selectOrganization(orgId) {
  state.org = orgId;
  state.orgSeq += 1;
  resetViews();
  $("org-title").textContent = orgId || "No organization";
  $("org-select").value = orgId || "";
  document.querySelectorAll(".org-item").forEach((item) => {
    if (item.dataset.org === orgId) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });
  if (orgId) {
    loadSummary().catch(report);
  }
  loadTab();
}

// ── Tabs ───────────────────────────────────────────────────────────────────

const loaders = {
  workers: loadWorkers,
  credentials: loadCredentials,
  jobs: (seq) => loadJobs(seq, false),
  settings: loadSettings,
  testbench: loadTestbench,
};

function setTab(name) {
  if (!TABS.includes(name)) {
    return;
  }
  state.tab = name;
  syncPolling();
  loadTab();
}

function loadTab() {
  state.seq += 1;
  const seq = state.seq;
  if (!state.token || !state.org) {
    return;
  }
  hideBanner();
  const tab = state.tab;
  setLoading(tab, seq, true);
  loaders[tab](seq)
    .catch(report)
    .finally(() => setLoading(tab, seq, false));
}

// ── Workers ────────────────────────────────────────────────────────────────

async function loadWorkers(seq) {
  const data = await api("GET", orgPath() + "/workers");
  if (seq !== state.seq) {
    return;
  }
  const items = (data && data.items) || [];
  const body = $("workers-body");
  body.replaceChildren();
  items.forEach((worker) => body.appendChild(workerRow(worker)));
  tallyWorkers(items);
  setEmpty("workers");
}

function workerStatus(worker) {
  let mark;
  if (worker.revoked) {
    mark = stamp("Revoked");
  } else if (worker.online) {
    mark = badge("Online", "success");
  } else {
    mark = badge("Offline", "neutral", "outlined");
  }
  const seen = el("span", "when-rel");
  if (worker.last_seen) {
    seen.appendChild(document.createTextNode("Last seen "));
    seen.appendChild(relativeTime(worker.last_seen));
    seen.title = worker.last_seen;
  } else {
    seen.textContent = "Never seen";
  }
  return stack(mark, seen);
}

function workerApproval(worker) {
  if (!worker.approved) {
    return badge("Not approved", "warning");
  }
  return badge(worker.approved_private ? "Approved private" : "Approved non-private", "success");
}

function workerRow(worker) {
  const row = el("tr", worker.revoked ? "is-revoked" : null);
  row.appendChild(cell("Name", worker.name ? el("span", "name", worker.name) : ""));
  row.appendChild(cell("ID", idWithCopy(worker.id, "Copy worker ID")));
  row.appendChild(cell("Status", workerStatus(worker)));
  row.appendChild(cell("Runtime", mono(worker.runtime_version)));
  row.appendChild(cell("Approval", workerApproval(worker)));
  row.appendChild(cell("Capabilities", capabilityTags(worker.reported_capabilities || [], worker.approved_capabilities || [])));

  const actions = el("div", "actions");
  if (!worker.revoked) {
    const privateButton = actionButton("Approve private", {
      variant: "brand",
      appearance: "accent",
      icon: "check",
      id: "approve-private-" + worker.id,
      onClick: (event) => approveWorker(event.currentTarget, worker, true),
    });
    const publicButton = actionButton("Approve non-private", {
      variant: "brand",
      appearance: "outlined",
      icon: "check",
      id: "approve-public-" + worker.id,
      onClick: (event) => approveWorker(event.currentTarget, worker, false),
    });
    actions.appendChild(privateButton);
    actions.appendChild(tooltipFor(privateButton, "Accepts its reported capabilities; it then runs private jobs only."));
    actions.appendChild(publicButton);
    actions.appendChild(tooltipFor(publicButton, "Accepts its reported capabilities; it then runs non-private jobs only."));
    actions.appendChild(
      actionButton("Revoke", {
        variant: "danger",
        icon: "ban",
        onClick: (event) => revokeWorker(event.currentTarget, worker),
      })
    );
  }
  row.appendChild(cell("Actions", actions.children.length ? actions : "", "actions-cell"));
  return row;
}

function workerPath(worker) {
  return orgPath() + "/workers/" + encodeURIComponent(worker.id);
}

function approveWorker(trigger, worker, isPrivate) {
  return run(trigger, async () => {
    await api("POST", workerPath(worker) + "/approve", { private: isPrivate });
    loadTab();
  });
}

async function revokeWorker(trigger, worker) {
  const label = worker.name ? '"' + worker.name + '"' : worker.id;
  const confirmed = await confirmAction({
    title: "Revoke worker?",
    message: "Revoke worker " + label + "? Its token stops working immediately.",
    confirmLabel: "Revoke worker",
    dismissLabel: "Keep worker",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    await api("POST", workerPath(worker) + "/revoke");
    loadTab();
  });
}

// ── Credentials ────────────────────────────────────────────────────────────

async function loadCredentials(seq) {
  const data = await api("GET", orgPath() + "/credentials");
  if (seq !== state.seq) {
    return;
  }
  const items = (data && data.items) || [];
  const body = $("credentials-body");
  body.replaceChildren();
  items.forEach((credential) => body.appendChild(credentialRow(credential)));
  tallyCredentials(items);
  setEmpty("credentials");
}

function credentialRow(credential) {
  const own = Boolean(state.me && credential.id === state.me.credential_id);
  const row = el("tr", credential.revoked_at ? "is-revoked" : null);
  row.appendChild(cell("Credential", idWithCopy(credential.id, "Copy credential ID")));
  row.appendChild(cell("Role", ROLE_LABELS[credential.role] || credential.role));
  row.appendChild(cell("Worker", credential.worker_id ? trackingNumber(credential.worker_id) : ""));
  row.appendChild(cell("Issued", timeNode(credential.created_at)));

  let standing;
  if (credential.revoked_at) {
    standing = stack(stamp("Revoked"), timeNode(credential.revoked_at));
  } else {
    standing = stack(badge("Live", "success"), own ? el("span", "when-rel", "This session") : null);
  }
  row.appendChild(cell("State", standing));

  const actions = el("div", "actions");
  if (!credential.revoked_at) {
    if (credential.role === "worker") {
      actions.appendChild(el("span", "quiet", "Revoke the worker instead"));
    } else {
      const revoke = actionButton("Revoke", {
        variant: "danger",
        icon: "ban",
        onClick: (event) => revokeCredential(event.currentTarget, credential),
      });
      if (own) {
        revoke.disabled = true;
        revoke.title = "You are signed in with this credential.";
        actions.appendChild(revoke);
        actions.appendChild(el("span", "quiet", "You are signed in with this credential."));
      } else {
        actions.appendChild(revoke);
      }
    }
  }
  row.appendChild(cell("Actions", actions.children.length ? actions : "", "actions-cell"));
  return row;
}

async function revokeCredential(trigger, credential) {
  const confirmed = await confirmAction({
    title: "Revoke token?",
    message: "Revoke credential " + credential.id + "? Anything using it stops working immediately.",
    confirmLabel: "Revoke token",
    dismissLabel: "Keep token",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    await api("POST", orgPath() + "/credentials/" + encodeURIComponent(credential.id) + "/revoke");
    loadTab();
  });
}

function issueCredential(trigger, role) {
  return run(trigger, async () => {
    const org = state.org;
    const issued = await api("POST", orgPath() + "/credentials", { role: role });
    if (org !== state.org || !state.token) {
      return;
    }
    showIssued(issued);
    if (state.tab === "credentials") {
      loadTab();
    }
  });
}

function showIssued(issued) {
  const slip = $("issued");
  slip.classList.remove("is-tearing");
  $("issued-id").replaceChildren(trackingNumber(issued.id));
  $("issued-role").textContent = ROLE_LABELS[issued.role] || issued.role;
  $("issued-token").value = issued.token;
  $("issued-copy").value = issued.token;
  $("issued-copied").hidden = true;
  slip.hidden = false;
  whenReady($("issued-token"), (input) => {
    input.focus();
    input.select();
  });
}

function hideIssued() {
  const slip = $("issued");
  slip.hidden = true;
  slip.classList.remove("is-tearing");
  $("issued-token").value = "";
  $("issued-copy").value = "";
  $("issued-role").textContent = "";
  $("issued-id").replaceChildren();
  $("issued-copied").hidden = true;
}

// The slip slides away before it is cleared; without motion it is cleared at once.
function tearOffIssued() {
  const slip = $("issued");
  if (slip.hidden || slip.classList.contains("is-tearing")) {
    return;
  }
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    hideIssued();
    $("issue-client").focus();
    return;
  }
  slip.classList.add("is-tearing");
  let done = false;
  const finish = () => {
    if (done) {
      return;
    }
    done = true;
    if (slip.classList.contains("is-tearing")) {
      hideIssued();
      $("issue-client").focus();
    }
  };
  const onEnd = (event) => {
    if (event.target === slip) {
      slip.removeEventListener("animationend", onEnd);
      finish();
    }
  };
  slip.addEventListener("animationend", onEnd);
  window.setTimeout(finish, 600);
}

// ── Jobs ───────────────────────────────────────────────────────────────────

function canManageJobs() {
  return Boolean(state.me && state.me.role === "org_admin" && state.org === state.me.organization_id);
}

async function loadJobs(seq, append) {
  let path = orgPath() + "/jobs";
  if (append && state.jobsCursor) {
    path += "?cursor=" + encodeURIComponent(state.jobsCursor);
  }
  const data = await api("GET", path);
  if (seq !== state.seq) {
    return;
  }
  const body = $("jobs-body");
  if (!append) {
    body.replaceChildren();
    state.jobs.clear();
  }
  $("jobs-actions-head").hidden = !canManageJobs();
  ((data && data.items) || []).forEach((job) => {
    state.jobs.set(job.job_id, job);
    body.appendChild(jobRow(job));
  });
  state.jobsCursor = (data && data.next_cursor) || null;
  $("jobs-more").hidden = !state.jobsCursor;
  tallyJobs();
  setEmpty("jobs");
}

function jobStatus(job) {
  switch (job.status) {
    case "pending":
      return badge("Pending", "warning");
    case "processing":
      return badge("Processing", "warning", "outlined");
    case "completed":
      return badge("Completed", "success");
    case "failed":
      return badge("Failed", "neutral", "outlined");
    case "cancelled":
      return stamp("Cancelled");
    default:
      return badge(String(job.status), "neutral", "outlined");
  }
}

// Thin bar: time since creation, full at 24 hours.
function waitBar(job) {
  const created = Date.parse(job.created_at);
  if (Number.isNaN(created)) {
    return null;
  }
  const share = Math.min(Math.max((Date.now() - created) / DAY_MS, 0), 1);
  const wrap = el("div", "wait");
  const track = el("span", "wait-track");
  track.setAttribute("aria-hidden", "true");
  track.style.setProperty("--wait", String(Math.max(share, 0.02)));
  track.appendChild(el("span", "wait-fill"));
  const label = el("span", "wait-label");
  label.title = "Bar is full at 24 hours.";
  label.appendChild(document.createTextNode("Created "));
  label.appendChild(relativeTime(job.created_at));
  wrap.appendChild(track);
  wrap.appendChild(label);
  return wrap;
}

function jobTimeline(job) {
  const list = el("dl", "timeline");
  [
    ["Created", job.created_at],
    ["Updated", job.updated_at],
    ["Deadline", job.deadline_at],
  ].forEach(([label, iso]) => {
    const item = el("div");
    item.appendChild(el("dt", "caps", label));
    const value = el("dd");
    value.appendChild(iso ? absoluteTime(iso) : dash());
    if (iso) {
      value.title = iso;
    }
    item.appendChild(value);
    list.appendChild(item);
  });
  return list;
}

function jobRow(job) {
  const waiting = job.status === "pending" || job.status === "processing";
  const row = el("tr", job.status === "cancelled" ? "is-revoked" : null);
  row.appendChild(cell("Job", idWithCopy(job.job_id, "Copy job ID")));
  row.appendChild(
    cell(
      "Kind & profile",
      stack(
        el("span", "nowrap", job.kind),
        el("span", "when-rel", job.private ? "Private" : "Non-private"),
        mono(job.execution_profile)
      )
    )
  );

  const reason = job.queue_reason ? el("span", "reason", QUEUE_REASONS[job.queue_reason] || job.queue_reason) : null;
  if (reason) {
    reason.title = job.queue_reason;
  }
  const attempt = isBlank(job.attempt) ? null : el("span", "when-rel", "Attempt ");
  if (attempt) {
    attempt.appendChild(numeral(job.attempt));
  }
  row.appendChild(cell("Status", stack(jobStatus(job), reason, attempt, waiting ? waitBar(job) : null)));

  let candidate = "";
  if (job.candidate) {
    candidate = mono(formatCandidate(job.candidate));
    candidate.classList.add("machine-wrap");
  }
  row.appendChild(cell("Candidate", candidate));
  row.appendChild(
    cell(
      "Error",
      job.error ? stack(mono(String(job.error.code)), el("span", "error-text", String(job.error.message))) : ""
    )
  );
  row.appendChild(cell("Result", job.result_available ? badge("Available", "success", "outlined") : ""));
  row.appendChild(cell("Timeline", jobTimeline(job)));

  if (canManageJobs()) {
    const actions = el("div", "actions");
    if (waiting) {
      actions.appendChild(
        actionButton("Cancel", {
          variant: "danger",
          icon: "x",
          onClick: (event) => cancelJob(event.currentTarget, row, job),
        })
      );
    }
    actions.appendChild(
      actionButton("Delete", {
        variant: "danger",
        appearance: "accent",
        icon: "trash",
        onClick: (event) => deleteJob(event.currentTarget, row, job),
      })
    );
    row.appendChild(cell("Actions", actions, "actions-cell"));
  }
  return row;
}

function jobPath(job) {
  return "/v1/jobs/" + encodeURIComponent(job.job_id);
}

async function cancelJob(trigger, row, job) {
  const confirmed = await confirmAction({
    title: "Cancel job?",
    message: "Cancel job " + job.job_id + "? This cannot be undone.",
    confirmLabel: "Cancel job",
    dismissLabel: "Keep job",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    const updated = await api("POST", jobPath(job) + "/cancel");
    if (row.isConnected && updated && updated.job_id !== undefined) {
      state.jobs.set(updated.job_id, updated);
      row.replaceWith(jobRow(updated));
      tallyJobs();
    } else {
      loadTab();
    }
  });
}

async function deleteJob(trigger, row, job) {
  const confirmed = await confirmAction({
    title: "Delete job?",
    message: "Delete job " + job.job_id + "? Its upload and result are removed.",
    confirmLabel: "Delete job",
    dismissLabel: "Keep job",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    await api("DELETE", jobPath(job));
    row.remove();
    state.jobs.delete(job.job_id);
    tallyJobs();
    setEmpty("jobs");
  });
}

function loadMoreJobs(trigger) {
  const seq = state.seq;
  return run(trigger, () => loadJobs(seq, true));
}

// ── Settings & baselines ───────────────────────────────────────────────────

async function loadSettings(seq) {
  const [settings, baselines] = await Promise.all([
    api("GET", orgPath() + "/settings"),
    api("GET", orgPath() + "/baselines"),
  ]);
  if (seq !== state.seq) {
    return;
  }
  $("settings-revision").textContent = String(settings.revision);
  $("settings-json").textContent = JSON.stringify(settings.settings, null, 2);

  const baselineBody = $("baselines-body");
  baselineBody.replaceChildren();
  ((baselines && baselines.items) || []).forEach((baseline) => {
    const row = el("tr");
    row.appendChild(cell("Execution profile", mono(baseline.execution_profile)));
    row.appendChild(cell("Platform", mono(baseline.platform)));
    row.appendChild(cell("Revision", numeral(baseline.revision)));
    row.appendChild(cell("Evaluation", baseline.evaluation_id ? trackingNumber(baseline.evaluation_id) : ""));
    row.appendChild(cell("Release", baseline.release_id ? trackingNumber(baseline.release_id) : ""));
    let sha = "";
    if (baseline.corpus_sha256) {
      sha = el("span", "id-copy");
      const short = mono(String(baseline.corpus_sha256).slice(0, 12));
      short.title = baseline.corpus_sha256;
      sha.appendChild(short);
      sha.appendChild(
        wa("wa-copy-button", {
          value: String(baseline.corpus_sha256),
          "copy-label": "Copy full SHA-256",
          "success-label": "Copied",
          "error-label": "Copy failed",
        })
      );
    }
    row.appendChild(cell("Corpus SHA-256", sha));
    row.appendChild(cell("Created", timeNode(baseline.created_at)));
    baselineBody.appendChild(row);
  });
  setEmpty("baselines");

  const releaseBody = $("releases-body");
  releaseBody.replaceChildren();
  ((baselines && baselines.approved_releases) || []).forEach((release) => {
    const row = el("tr");
    row.appendChild(cell("Execution profile", mono(release.execution_profile)));
    row.appendChild(cell("Platform", mono(release.platform)));
    row.appendChild(cell("Release", release.release_id ? trackingNumber(release.release_id) : ""));
    row.appendChild(cell("Evaluation", release.evaluation_id ? trackingNumber(release.evaluation_id) : ""));
    row.appendChild(cell("Approved", timeNode(release.approved_at)));
    releaseBody.appendChild(row);
  });
  setEmpty("releases");
}

// ── Testbench ──────────────────────────────────────────────────────────────

const TESTBENCH_API = "/v1/testbench";
const SETTINGS_API = "/v1/settings";
const POLL_MS = 5000;
const ITEMS_PAGE = 50;
const MAX_RUN_CANDIDATES = 16;
const ITEM_STATUSES = {
  awaiting_draft: { label: "Awaiting draft", variant: "neutral", appearance: "outlined" },
  needs_correction: { label: "Needs correction", variant: "warning", appearance: "filled-outlined" },
  corrected: { label: "Corrected", variant: "success", appearance: "filled-outlined" },
};
const FILE_EXTENSIONS = {
  "application/pdf": "pdf",
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/tiff": "tif",
  "audio/wav": "wav",
  "audio/x-wav": "wav",
  "audio/mpeg": "mp3",
  "audio/mp4": "m4a",
  "audio/x-m4a": "m4a",
  "audio/flac": "flac",
  "audio/ogg": "ogg",
  "audio/webm": "webm",
};

const tb = {
  cursor: null,
  // Bumped per item-list load; older responses are dropped.
  listSeq: 0,
  // Item id -> its table row.
  rows: new Map(),
  created: null,
  // The open item (inline detail section); null when closed.
  openId: null,
  // Bumped whenever the detail opens or closes; responses for an older one are dropped.
  detailSeq: 0,
  detail: null,
  // The user edited the reference text since it was last seeded or saved.
  dirty: false,
  candidates: [],
  selected: new Set(),
  blob: null,
  objectUrl: null,
  timer: null,
  diffRunId: null,
  diffSeq: 0,
  diffSig: "",
  // Signature of the item and its runs at the last render; a change refreshes the aggregates.
  runSig: null,
};

// The testbench reads job content, so only an org admin in their own organization may use it.
function testbenchAllowed() {
  return canManageJobs();
}

function itemPath(id) {
  return TESTBENCH_API + "/items/" + encodeURIComponent(id);
}

// Runs a testbench action; a switched-off testbench (403) sends the tab back to its status.
function tbAction(trigger, action) {
  return run(trigger, async () => {
    try {
      await action();
    } catch (error) {
      if (error && error.code === "testbench_disabled") {
        loadTab();
      }
      throw error;
    }
  });
}

function showTestbenchView(mode, message) {
  $("tb-forbidden").hidden = mode !== "forbidden";
  $("tb-forbidden").textContent = mode === "forbidden" ? message || "" : "";
  $("tb-content").hidden = mode === "forbidden" || mode === "none";
  $("tb-off").hidden = mode !== "off";
  $("tb-main").hidden = mode !== "on";
  $("tb-disable").hidden = mode !== "on";
}

function renderTestbenchStatus(status) {
  $("tb-state").replaceChildren(status.enabled ? badge("Enabled", "success") : badge("Disabled", "neutral", "outlined"));
  $("tb-count-items").textContent = String(status.items);
  $("tb-count-corrected").textContent = String(status.corrected);
  $("tb-count-runs").textContent = String(status.runs);
  showTestbenchView(status.enabled ? "on" : "off");
}

async function loadTestbench(seq) {
  if (!testbenchAllowed()) {
    closeDetail();
    showTestbenchView(
      "forbidden",
      "The testbench stores and shows job content (recordings, scans and their texts), so it is closed to the hub operator role. Sign in with an organization admin token to use it for that organization."
    );
    return;
  }
  let status;
  try {
    status = await api("GET", TESTBENCH_API + "/status");
  } catch (error) {
    if (error.status === 403 && !error.handled) {
      closeDetail();
      showTestbenchView("forbidden", error.message);
      return;
    }
    throw error;
  }
  if (seq !== state.seq) {
    return;
  }
  renderTestbenchStatus(status);
  if (!status.enabled) {
    closeDetail();
    return;
  }
  await Promise.all([loadItems(seq, false), loadLeaderboard(seq)]);
}

// Re-reads the counters and the leaderboard after something changed them.
function refreshAggregates() {
  const seq = state.seq;
  api("GET", TESTBENCH_API + "/status")
    .then((status) => {
      if (seq === state.seq && status) {
        renderTestbenchStatus(status);
      }
    })
    .catch(report);
  loadLeaderboard(seq).catch(report);
}

function setTestbenchEnabled(trigger, enabled) {
  return (async () => {
    const confirmed = await confirmAction(
      enabled
        ? {
            title: "Enable testbench?",
            message:
              "Recordings and scans contributed to the testbench are stored on the hub persistently, until you delete the item. Enable it for " +
              state.org +
              "?",
            confirmLabel: "Enable testbench",
            dismissLabel: "Keep it off",
          }
        : {
            title: "Disable testbench?",
            message:
              "Existing items, references and runs are kept on the hub, but nothing can be added or read until the testbench is enabled again.",
            confirmLabel: "Disable testbench",
            dismissLabel: "Keep it on",
          }
    );
    if (!confirmed) {
      return;
    }
    await run(trigger, async () => {
      const current = await apiWithEtag(SETTINGS_API);
      if (!current.etag || !current.data || !current.data.settings) {
        throw new ApiError(0, "settings_unreadable", "The hub did not return the organization settings.");
      }
      const settings = Object.assign({}, current.data.settings, { testbench_enabled: enabled });
      try {
        await request("PUT", SETTINGS_API, { json: settings, headers: { "If-Match": current.etag } });
      } catch (error) {
        if (error.status === 409) {
          loadTab();
          throw new ApiError(
            409,
            "revision_conflict",
            "The settings changed in the meantime, so nothing was changed. The status below is up to date; try again."
          );
        }
        throw error;
      }
      loadTab();
    });
  })();
}

function resetTestbench() {
  closeDetail();
  tb.cursor = null;
  tb.listSeq += 1;
  tb.rows.clear();
  tb.created = null;
  $("tb-created").hidden = true;
  $("tb-file").value = "";
  $("tb-label").value = "";
  $("tb-state").replaceChildren();
  ["items", "corrected", "runs"].forEach((name) => {
    $("tb-count-" + name).textContent = "–";
  });
  $("tb-lb-corrected").textContent = "";
  $("items-more").hidden = true;
  showTestbenchView("none");
}

// ── Testbench: contribute and item list ────────────────────────────────────

function contribute(event) {
  event.preventDefault();
  const input = $("tb-file");
  const file = input.files && input.files[0];
  if (!file) {
    showError("Choose a recording or scan to contribute.");
    return Promise.resolve();
  }
  const form = new FormData();
  form.append("file", file);
  form.append("language", String($("tb-language").value || "de"));
  const label = String($("tb-label").value || "").trim();
  if (label) {
    form.append("label", label);
  }
  const orgSeq = state.orgSeq;
  return tbAction($("tb-submit"), async () => {
    const item = await apiUpload(TESTBENCH_API + "/items", form);
    if (orgSeq !== state.orgSeq || !item) {
      return;
    }
    input.value = "";
    $("tb-label").value = "";
    tb.created = item;
    $("tb-created-text").textContent =
      "Item #" + item.id + " created (" + item.kind + ", " + item.language + (item.label ? ", “" + item.label + "”" : "") + "). Its draft run is queued.";
    $("tb-created").hidden = false;
    loadTab();
  });
}

function itemFilters() {
  const params = new URLSearchParams({ limit: String(ITEMS_PAGE) });
  const kind = $("tb-filter-kind").value;
  const status = $("tb-filter-status").value;
  if (kind && kind !== "all") {
    params.set("kind", kind);
  }
  if (status && status !== "all") {
    params.set("status", status);
  }
  return params;
}

async function loadItems(seq, append) {
  const listSeq = (tb.listSeq += 1);
  const params = itemFilters();
  if (append && tb.cursor) {
    params.set("cursor", tb.cursor);
  }
  const data = await api("GET", TESTBENCH_API + "/items?" + params.toString());
  if (seq !== state.seq || listSeq !== tb.listSeq) {
    return;
  }
  const body = $("items-body");
  if (!append) {
    body.replaceChildren();
    tb.rows.clear();
  }
  ((data && data.items) || []).forEach((item) => {
    const row = itemRow(item);
    tb.rows.set(item.id, row);
    body.appendChild(row);
  });
  tb.cursor = (data && data.next_cursor) || null;
  $("items-more").hidden = !tb.cursor;
  setEmpty("items");
}

function reloadItems() {
  tb.cursor = null;
  const seq = state.seq;
  setLoading("testbench", seq, true);
  loadItems(seq, false)
    .catch(report)
    .finally(() => setLoading("testbench", seq, false));
}

function itemStatus(item) {
  const known = ITEM_STATUSES[item.status];
  const mark = known ? badge(known.label, known.variant, known.appearance) : badge(String(item.status), "neutral", "outlined");
  const note = item.draft_status === "failed" ? el("span", "when-rel", "Draft run failed") : null;
  return stack(mark, note);
}

function itemRow(item) {
  const row = el("tr", item.id === tb.openId ? "is-open" : null);
  row.appendChild(cell("Item", numeral("#" + item.id)));
  row.appendChild(cell("Status", itemStatus(item)));
  row.appendChild(cell("Kind", item.kind));
  row.appendChild(cell("Language", mono(item.language)));
  row.appendChild(cell("Label", item.label ? el("span", "label-text", item.label) : ""));
  row.appendChild(cell("Size", isBlank(item.size) ? "" : wa("wa-format-bytes", { value: item.size })));
  row.appendChild(cell("Runs", numeral(item.run_count)));
  row.appendChild(cell("Created", timeNode(item.created_at)));
  const actions = el("div", "actions");
  actions.appendChild(
    actionButton("Open", {
      variant: "brand",
      appearance: "outlined",
      icon: "chevron-right",
      onClick: (event) => openItem(item.id, event.currentTarget),
    })
  );
  row.appendChild(cell("Actions", actions, "actions-cell"));
  return row;
}

function markOpenRow() {
  tb.rows.forEach((row, id) => row.classList.toggle("is-open", id === tb.openId));
}

// ── Testbench: item detail ─────────────────────────────────────────────────

async function openItem(id, trigger) {
  stopPolling();
  tb.detailSeq += 1;
  tb.diffSeq += 1;
  const seq = tb.detailSeq;
  releasePreview();
  tb.openId = id;
  tb.detail = null;
  tb.dirty = false;
  tb.diffRunId = null;
  tb.diffSig = "";
  tb.runSig = null;
  tb.selected.clear();
  tb.candidates = [];
  markOpenRow();
  $("tb-diff").hidden = true;
  $("runs-body").replaceChildren();
  $("runs-empty").hidden = true;
  $("tb-detail-title").textContent = "Item #" + id;
  $("tb-detail-meta").textContent = "";
  $("tb-reference").value = "";
  $("tb-reference-note").textContent = "";
  $("tb-candidates").replaceChildren();
  $("tb-candidates-empty").hidden = true;
  updateRunButton();
  updateSaveButton();
  $("tb-detail").hidden = false;
  $("tb-detail").scrollIntoView({ block: "start" });
  await tbAction(trigger, async () => {
    $("tb-detail-spinner").hidden = false;
    try {
      const detail = await api("GET", itemPath(id));
      if (seq !== tb.detailSeq) {
        return;
      }
      tb.detail = detail;
      renderDetail(detail, true);
      loadPreview(detail, seq);
      loadCandidates(seq);
    } catch (error) {
      if (error.status === 404 && seq === tb.detailSeq) {
        itemGone();
        return;
      }
      throw error;
    } finally {
      if (seq === tb.detailSeq) {
        $("tb-detail-spinner").hidden = true;
      }
    }
  });
}

function closeDetail() {
  tb.detailSeq += 1;
  tb.diffSeq += 1;
  stopPolling();
  releasePreview();
  tb.openId = null;
  tb.detail = null;
  tb.dirty = false;
  tb.diffRunId = null;
  tb.diffSig = "";
  tb.runSig = null;
  tb.selected.clear();
  tb.candidates = [];
  $("tb-detail").hidden = true;
  $("tb-diff").hidden = true;
  $("tb-diff-text").replaceChildren();
  $("runs-body").replaceChildren();
  $("tb-candidates").replaceChildren();
  markOpenRow();
}

function itemGone() {
  closeDetail();
  showError("That item no longer exists.");
  loadTab();
}

async function refreshDetail() {
  const id = tb.openId;
  if (id === null) {
    return;
  }
  const seq = tb.detailSeq;
  let detail;
  try {
    detail = await api("GET", itemPath(id));
  } catch (error) {
    if (error.status === 404 && seq === tb.detailSeq) {
      itemGone();
      return;
    }
    throw error;
  }
  if (seq !== tb.detailSeq) {
    return;
  }
  tb.detail = detail;
  renderDetail(detail, false);
}

function runIsActive(run) {
  return run.status === "queued" || run.status === "processing";
}

function detailSignature(detail) {
  return [detail.status, detail.reference_set_at]
    .concat((detail.runs || []).map((run) => [run.id, run.status, run.wer, run.cer].join(":")))
    .join("|");
}

function renderDetail(detail, initial) {
  $("tb-detail-title").textContent = "Item #" + detail.id;
  const known = ITEM_STATUSES[detail.status];
  const meta = [detail.kind, detail.language];
  if (detail.label) {
    meta.push("“" + detail.label + "”");
  }
  meta.push(known ? known.label : String(detail.status));
  if (detail.sha256) {
    meta.push("SHA-256 " + String(detail.sha256).slice(0, 12));
  }
  $("tb-detail-meta").textContent = meta.join(" · ");

  const row = tb.rows.get(detail.id);
  if (row && row.isConnected) {
    const fresh = itemRow(detail);
    row.replaceWith(fresh);
    tb.rows.set(detail.id, fresh);
  }

  // The editor starts from the saved reference, else the draft output; it is never
  // overwritten while the user has unsaved edits.
  const hasReference = detail.reference !== null && detail.reference !== undefined;
  const draftText = detail.draft && typeof detail.draft.text === "string" ? detail.draft.text : null;
  if (initial || !tb.dirty) {
    const seed = hasReference ? detail.reference : draftText || "";
    if ($("tb-reference").value !== seed) {
      $("tb-reference").value = seed;
    }
    tb.dirty = false;
  }
  let note;
  if (hasReference) {
    note = "Saved reference" + (detail.reference_set_at ? " (" + detail.reference_set_at + ")" : "") + ". Saving again re-scores every completed run.";
  } else if (draftText !== null) {
    note =
      "Prefilled with the draft run's output. Correct every word so it matches the original, then save: it becomes the ground truth the candidates are scored against.";
  } else {
    const draftStatus = detail.draft ? detail.draft.status : detail.draft_status;
    note = "The draft run has not produced text yet" + (draftStatus ? " (" + draftStatus + ")" : "") + ". You can type the reference yourself.";
  }
  $("tb-reference-note").textContent = note;
  updateSaveButton();

  const runs = (detail.runs || []).slice().sort((a, b) => a.id - b.id);
  const body = $("runs-body");
  body.replaceChildren();
  runs.forEach((run) => body.appendChild(runRow(run)));
  setEmpty("runs");

  const signature = detailSignature(detail);
  if (!initial && tb.runSig !== null && signature !== tb.runSig) {
    refreshAggregates();
  }
  tb.runSig = signature;

  if (tb.diffRunId !== null) {
    const open = runs.find((run) => run.id === tb.diffRunId);
    if (!open) {
      hideDiff();
    } else if ([open.status, open.wer, open.cer, detail.reference_set_at].join(":") !== tb.diffSig) {
      showDiff(open.id, false);
    }
  }
  syncPolling();
}

function updateSaveButton() {
  $("tb-save-reference").disabled = $("tb-reference").value === "";
}

function saveReference(trigger) {
  const id = tb.openId;
  const text = $("tb-reference").value;
  if (id === null || text === "") {
    return Promise.resolve();
  }
  return tbAction(trigger, async () => {
    const detail = await api("PUT", itemPath(id) + "/reference", { text: text });
    if (id !== tb.openId || !detail) {
      return;
    }
    tb.dirty = false;
    tb.detail = detail;
    renderDetail(detail, false);
  }).then(updateSaveButton);
}

async function deleteItem(trigger) {
  const id = tb.openId;
  if (id === null) {
    return;
  }
  const confirmed = await confirmAction({
    title: "Delete item?",
    message: "Delete item #" + id + "? Its stored original, reference and every run are removed, and active runs are cancelled. This cannot be undone.",
    confirmLabel: "Delete item",
    dismissLabel: "Keep item",
  });
  if (!confirmed || id !== tb.openId) {
    return;
  }
  await tbAction(trigger, async () => {
    await api("DELETE", itemPath(id));
    closeDetail();
    loadTab();
  });
}

// ── Testbench: original preview ────────────────────────────────────────────

function releasePreview() {
  if (tb.objectUrl) {
    URL.revokeObjectURL(tb.objectUrl);
  }
  tb.objectUrl = null;
  tb.blob = null;
  $("tb-preview").replaceChildren();
}

function previewKind(detail) {
  const type = String(detail.content_type || "").toLowerCase();
  if (type.startsWith("audio/") || (!type && detail.kind === "audio")) {
    return "audio";
  }
  if (type === "image/png" || type === "image/jpeg") {
    return "image";
  }
  return null;
}

function previewNote(text) {
  $("tb-preview").replaceChildren(el("p", "quiet", text));
}

function originalProblem(error) {
  return error.code === "content_unavailable" ? "The original file is no longer available on the hub." : error.message;
}

async function fetchOriginal(detail, seq) {
  if (tb.blob) {
    return tb.blob;
  }
  const blob = await apiBlob(itemPath(detail.id) + "/file");
  if (seq !== tb.detailSeq) {
    return null;
  }
  tb.blob = blob;
  tb.objectUrl = URL.createObjectURL(blob);
  return blob;
}

// Audio and PNG/JPEG scans preview inline from a blob; PDFs and TIFFs only download.
async function loadPreview(detail, seq) {
  const kind = previewKind(detail);
  if (!kind) {
    previewNote("No inline preview for " + (detail.content_type || "this file type") + ". Use Download original.");
    return;
  }
  previewNote("Loading the original…");
  try {
    if (!(await fetchOriginal(detail, seq))) {
      return;
    }
  } catch (error) {
    if (seq === tb.detailSeq) {
      previewNote(originalProblem(error));
    }
    return;
  }
  let node;
  if (kind === "audio") {
    node = el("audio", "original-audio");
    node.controls = true;
    node.preload = "metadata";
    node.addEventListener("error", () => previewNote("This browser cannot play the original. Use Download original."));
  } else {
    node = el("img", "original-image");
    node.alt = "Original scan of item " + detail.id;
    node.addEventListener("error", () => previewNote("This browser cannot show the original. Use Download original."));
  }
  node.src = tb.objectUrl;
  $("tb-preview").replaceChildren(node);
}

function originalFileName(detail) {
  const type = String(detail.content_type || "").toLowerCase();
  let extension = FILE_EXTENSIONS[type];
  if (!extension) {
    extension = type.includes("/") ? type.split("/")[1].replace(/[^a-z0-9]/g, "") : "";
  }
  return "testbench-item-" + detail.id + "." + (extension || "bin");
}

function downloadOriginal(trigger) {
  const detail = tb.detail;
  if (!detail) {
    return Promise.resolve();
  }
  const seq = tb.detailSeq;
  return tbAction(trigger, async () => {
    if (!(await fetchOriginal(detail, seq))) {
      return;
    }
    const link = document.createElement("a");
    link.href = tb.objectUrl;
    link.download = originalFileName(detail);
    document.body.appendChild(link);
    link.click();
    link.remove();
  });
}

// ── Testbench: candidates and runs ─────────────────────────────────────────

async function loadCandidates(seq) {
  try {
    const data = await api("GET", TESTBENCH_API + "/candidates");
    if (seq !== tb.detailSeq) {
      return;
    }
    tb.candidates = (data && data.items) || [];
  } catch (error) {
    if (seq !== tb.detailSeq) {
      return;
    }
    tb.candidates = [];
    report(error);
  }
  renderCandidates();
}

function renderCandidates() {
  const box = $("tb-candidates");
  box.replaceChildren();
  const kind = tb.detail ? tb.detail.kind : null;
  const matching = tb.candidates.filter((entry) => entry.kind === kind);
  const keys = new Set(matching.map((entry) => capabilityKey(entry.candidate)));
  Array.from(tb.selected).forEach((key) => {
    if (!keys.has(key)) {
      tb.selected.delete(key);
    }
  });
  const empty = $("tb-candidates-empty");
  if (!matching.length) {
    empty.textContent =
      "No " + (kind || "") + " candidate is available. Approve a private worker on the Workers tab; candidates appear once it has reported them as tested.";
    empty.hidden = false;
    updateRunButton();
    return;
  }
  empty.hidden = true;
  const group = el("div", "candidate-group");
  group.appendChild(el("h5", "caps", kind));
  matching.forEach((entry) => {
    const key = capabilityKey(entry.candidate);
    const line = el("div", "candidate-row");
    const box = wa("wa-checkbox", { checked: tb.selected.has(key), size: "s" }, formatCandidate(entry.candidate));
    box.addEventListener("change", (event) => {
      if (event.target.checked) {
        tb.selected.add(key);
      } else {
        tb.selected.delete(key);
      }
      updateRunButton();
    });
    line.appendChild(box);
    const online = Number(entry.workers_online) || 0;
    line.appendChild(el("span", "when-rel", online + (online === 1 ? " worker online" : " workers online")));
    group.appendChild(line);
  });
  box.appendChild(group);
  updateRunButton();
}

function updateRunButton() {
  const count = tb.selected.size;
  $("tb-run").disabled = count === 0 || count > MAX_RUN_CANDIDATES;
  $("tb-run-count").textContent =
    count > MAX_RUN_CANDIDATES ? count + " selected; at most " + MAX_RUN_CANDIDATES + " per request" : count ? count + " selected" : "";
}

function runSelected(trigger) {
  const id = tb.openId;
  if (id === null || !tb.detail) {
    return Promise.resolve();
  }
  const chosen = tb.candidates
    .filter((entry) => entry.kind === tb.detail.kind && tb.selected.has(capabilityKey(entry.candidate)))
    .map((entry) => entry.candidate);
  if (!chosen.length) {
    return Promise.resolve();
  }
  return tbAction(trigger, async () => {
    const created = await api("POST", itemPath(id) + "/runs", { candidates: chosen });
    if (id !== tb.openId) {
      return;
    }
    const queued = created && created.items ? created.items.length : 0;
    tb.selected.clear();
    renderCandidates();
    const skipped = chosen.length - queued;
    $("tb-run-count").textContent =
      "Queued " + queued + (queued === 1 ? " run" : " runs") + (skipped > 0 ? "; " + skipped + " already running was skipped" : "");
    await refreshDetail();
  }).then(() => {
    const note = $("tb-run-count").textContent;
    updateRunButton();
    if (note.startsWith("Queued") && !tb.selected.size) {
      $("tb-run-count").textContent = note;
    }
  });
}

function percentNode(value) {
  return isBlank(value) ? dash() : el("span", "num", (Number(value) * 100).toFixed(1) + " %");
}

function secondsNode(value) {
  return isBlank(value) ? dash() : el("span", "num", Number(value).toFixed(1));
}

function runStatus(run) {
  switch (run.status) {
    case "queued":
      return badge("Queued", "warning");
    case "processing":
      return badge("Processing", "warning", "outlined");
    case "completed":
      return badge("Completed", "success");
    case "failed":
      return badge("Failed", "neutral", "outlined");
    case "cancelled":
      return stamp("Cancelled");
    default:
      return badge(String(run.status), "neutral", "outlined");
  }
}

function runRow(run) {
  const row = el("tr", run.id === tb.diffRunId ? "is-selected is-clickable" : "is-clickable");
  row.dataset.run = String(run.id);
  row.appendChild(cell("Run", numeral("#" + run.id)));
  const role = badge(run.role === "draft" ? "Draft" : "Bench", run.role === "draft" ? "neutral" : "brand", "outlined");
  if (run.role === "draft") {
    role.title = "The reference started from this output, so the draft favours itself and is left out of the leaderboard.";
  }
  row.appendChild(cell("Role", role));
  const candidate = mono(formatCandidate(run.candidate));
  candidate.classList.add("machine-wrap");
  row.appendChild(cell("Candidate", candidate));
  row.appendChild(cell("Device", mono(run.device)));
  row.appendChild(cell("Seconds", secondsNode(run.seconds)));
  row.appendChild(cell("WER", percentNode(run.wer)));
  row.appendChild(cell("CER", percentNode(run.cer)));
  const reason = run.queue_reason ? el("span", "reason", QUEUE_REASONS[run.queue_reason] || run.queue_reason) : null;
  row.appendChild(cell("Status", stack(runStatus(run), reason, run.error_code ? mono(run.error_code) : null)));
  const actions = el("div", "actions");
  actions.appendChild(
    actionButton("Word diff", {
      icon: "diff",
      onClick: () => showDiff(run.id, true),
    })
  );
  actions.appendChild(
    actionButton("Delete", {
      variant: "danger",
      icon: "trash",
      onClick: (event) => deleteRun(event.currentTarget, run),
    })
  );
  row.appendChild(cell("Actions", actions, "actions-cell"));
  row.addEventListener("click", (event) => {
    if (!event.target.closest("wa-button")) {
      showDiff(run.id, true);
    }
  });
  return row;
}

async function deleteRun(trigger, run) {
  const id = tb.openId;
  const confirmed = await confirmAction({
    title: "Delete run?",
    message: "Delete run #" + run.id + " (" + formatCandidate(run.candidate) + ")? A queued or processing run is cancelled.",
    confirmLabel: "Delete run",
    dismissLabel: "Keep run",
  });
  if (!confirmed || id !== tb.openId) {
    return;
  }
  await tbAction(trigger, async () => {
    await api("DELETE", itemPath(id) + "/runs/" + encodeURIComponent(run.id));
    if (id !== tb.openId) {
      return;
    }
    if (tb.diffRunId === run.id) {
      hideDiff();
    }
    await refreshDetail();
  });
}

// While a run is queued or processing and the item is open, the detail refreshes every
// POLL_MS. Polling stops on close, on another tab and while the page is hidden.
function stopPolling() {
  if (tb.timer !== null) {
    window.clearTimeout(tb.timer);
    tb.timer = null;
  }
}

function syncPolling() {
  stopPolling();
  if (
    tb.openId === null ||
    !tb.detail ||
    document.hidden ||
    state.tab !== "testbench" ||
    !(tb.detail.runs || []).some(runIsActive)
  ) {
    return;
  }
  const seq = tb.detailSeq;
  tb.timer = window.setTimeout(() => {
    tb.timer = null;
    if (seq !== tb.detailSeq) {
      return;
    }
    refreshDetail().catch(report);
  }, POLL_MS);
}

// ── Testbench: word diff ───────────────────────────────────────────────────

function hideDiff() {
  tb.diffRunId = null;
  tb.diffSeq += 1;
  tb.diffSig = "";
  $("tb-diff").hidden = true;
  $("tb-diff-text").replaceChildren();
  document.querySelectorAll("#runs-body tr.is-selected").forEach((row) => row.classList.remove("is-selected"));
}

async function showDiff(runId, reveal) {
  const id = tb.openId;
  if (id === null) {
    return;
  }
  tb.diffRunId = runId;
  const seq = (tb.diffSeq += 1);
  const summary = tb.detail && (tb.detail.runs || []).find((run) => run.id === runId);
  if (summary) {
    tb.diffSig = [summary.status, summary.wer, summary.cer, tb.detail.reference_set_at].join(":");
  }
  document.querySelectorAll("#runs-body tr").forEach((row) => {
    row.classList.toggle("is-selected", row.dataset.run === String(runId));
  });
  $("tb-diff").hidden = false;
  if (reveal) {
    $("tb-diff").scrollIntoView({ block: "nearest" });
  }
  try {
    const detail = await api("GET", itemPath(id) + "/runs/" + encodeURIComponent(runId));
    if (seq !== tb.diffSeq || id !== tb.openId) {
      return;
    }
    renderDiff(detail);
  } catch (error) {
    if (seq !== tb.diffSeq) {
      return;
    }
    if (error.status === 404) {
      hideDiff();
    }
    report(error);
  }
}

function diffNote(text) {
  const note = $("tb-diff-note");
  note.textContent = text || "";
  note.hidden = !text;
}

function renderDiff(run) {
  $("tb-diff-title").textContent = "Word diff · run #" + run.id;
  const text = $("tb-diff-text");
  text.replaceChildren();
  const warnings = $("tb-diff-warnings");
  warnings.replaceChildren();
  (run.warnings || []).forEach((warning) => warnings.appendChild(el("li", null, warning)));

  const summary = [formatCandidate(run.candidate)];
  if (!isBlank(run.wer)) {
    summary.push("WER " + (Number(run.wer) * 100).toFixed(1) + " %");
  }
  if (!isBlank(run.cer)) {
    summary.push("CER " + (Number(run.cer) * 100).toFixed(1) + " %");
  }
  if (run.role === "draft") {
    summary.push("draft run: biased towards the reference");
  }

  const alignment = run.alignment;
  if (!Array.isArray(alignment)) {
    $("tb-diff-summary").textContent = summary.join(" · ");
    const hasReference = tb.detail && tb.detail.reference !== null && tb.detail.reference !== undefined;
    diffNote(
      run.status !== "completed"
        ? "No word diff yet: this run is " + run.status + "."
        : !hasReference
          ? "No word diff yet: save a reference first."
          : "This run has no alignment."
    );
    return;
  }
  diffNote("");
  const counts = { equal: 0, substitute: 0, insert: 0, delete: 0 };
  const fragment = document.createDocumentFragment();
  alignment.forEach((entry, index) => {
    if (index > 0) {
      fragment.appendChild(document.createTextNode(" "));
    }
    if (counts[entry.op] !== undefined) {
      counts[entry.op] += 1;
    }
    switch (entry.op) {
      case "equal":
        fragment.appendChild(el("span", "diff-equal", entry.ref !== null && entry.ref !== undefined ? entry.ref : entry.hyp));
        break;
      case "substitute": {
        const pair = el("span", "diff-substitute");
        pair.appendChild(el("span", "diff-ref", entry.ref));
        pair.appendChild(document.createTextNode(" "));
        pair.appendChild(el("span", "diff-hyp", entry.hyp));
        fragment.appendChild(pair);
        break;
      }
      case "insert":
        fragment.appendChild(el("span", "diff-insert diff-hyp", entry.hyp));
        break;
      case "delete":
        fragment.appendChild(el("span", "diff-delete diff-ref", entry.ref));
        break;
      default:
        fragment.appendChild(el("span", "diff-equal", entry.ref !== null && entry.ref !== undefined ? entry.ref : entry.hyp));
    }
  });
  text.appendChild(fragment);
  summary.push(
    counts.equal + " equal, " + counts.substitute + " replaced, " + counts.insert + " added, " + counts.delete + " missing"
  );
  $("tb-diff-summary").textContent = summary.join(" · ");
}

// ── Testbench: leaderboard ─────────────────────────────────────────────────

async function loadLeaderboard(seq) {
  const data = await api("GET", TESTBENCH_API + "/leaderboard");
  if (seq !== state.seq) {
    return;
  }
  renderLeaderboard(data || {});
}

function renderLeaderboard(data) {
  const corrected = Number(data.corrected_items) || 0;
  $("tb-lb-corrected").textContent = corrected + (corrected === 1 ? " corrected item" : " corrected items") + " in the testbench.";
  const entries = data.items || [];
  ["audio", "document"].forEach((kind) => {
    const rows = entries.filter((entry) => entry.kind === kind);
    const bestOf = (field) => {
      const values = rows.map((entry) => entry[field]).filter((value) => !isBlank(value));
      return rows.length > 1 && values.length ? Math.min(...values) : null;
    };
    const best = { wer_mean: bestOf("wer_mean"), cer_mean: bestOf("cer_mean"), seconds_mean: bestOf("seconds_mean") };
    const body = $("lb-" + kind + "-body");
    body.replaceChildren();
    rows.forEach((entry) => {
      const row = el("tr");
      const candidate = mono(formatCandidate(entry.candidate));
      candidate.classList.add("machine-wrap");
      row.appendChild(cell("Candidate", candidate));
      row.appendChild(cell("Items (n)", numeral(entry.items)));
      const metric = (label, field, format) => {
        const value = entry[field];
        const node = isBlank(value) ? dash() : el("span", "num", format(Number(value)));
        if (!isBlank(value) && best[field] !== null && Number(value) === best[field]) {
          node.classList.add("best");
          node.title = "Best in column";
        }
        row.appendChild(cell(label, node));
      };
      metric("WER mean", "wer_mean", (value) => (value * 100).toFixed(1) + " %");
      metric("CER mean", "cer_mean", (value) => (value * 100).toFixed(1) + " %");
      metric("Mean seconds", "seconds_mean", (value) => value.toFixed(1));
      body.appendChild(row);
    });
    setEmpty("lb-" + kind);
  });
}

function wireTestbench() {
  $("tb-contribute").addEventListener("submit", contribute);
  $("tb-created-open").addEventListener("click", (event) => {
    if (tb.created) {
      openItem(tb.created.id, event.currentTarget);
    }
  });
  ["tb-filter-kind", "tb-filter-status"].forEach((id) => {
    $(id).addEventListener("change", (event) => {
      if (event.target === event.currentTarget) {
        reloadItems();
      }
    });
  });
  $("items-more").addEventListener("click", (event) => {
    const seq = state.seq;
    tbAction(event.currentTarget, () => loadItems(seq, true));
  });
  $("tb-enable").addEventListener("click", (event) => setTestbenchEnabled(event.currentTarget, true));
  $("tb-disable").addEventListener("click", (event) => setTestbenchEnabled(event.currentTarget, false));
  $("tb-detail-close").addEventListener("click", closeDetail);
  $("tb-detail-delete").addEventListener("click", (event) => deleteItem(event.currentTarget));
  $("tb-detail-refresh").addEventListener("click", (event) =>
    tbAction(event.currentTarget, async () => {
      await refreshDetail();
      if (tb.openId !== null) {
        await loadCandidates(tb.detailSeq);
      }
    })
  );
  $("tb-download").addEventListener("click", (event) => downloadOriginal(event.currentTarget));
  $("tb-reference").addEventListener("input", () => {
    tb.dirty = true;
    updateSaveButton();
  });
  $("tb-save-reference").addEventListener("click", (event) => saveReference(event.currentTarget));
  $("tb-run").addEventListener("click", (event) => runSelected(event.currentTarget));
  $("tb-candidates-refresh").addEventListener("click", (event) => {
    if (tb.openId !== null) {
      tbAction(event.currentTarget, () => loadCandidates(tb.detailSeq));
    }
  });
  document.addEventListener("visibilitychange", syncPolling);
}

// ── Wiring ─────────────────────────────────────────────────────────────────

function init() {
  $("banner-close").addEventListener("click", hideBanner);

  $("login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("login-token");
    const token = String(input.value || "").trim();
    input.value = "";
    if (!token) {
      return;
    }
    hideBanner();
    $("login-message").hidden = true;
    state.token = token;
    saveToken(token);
    enter();
  });

  $("sign-out").addEventListener("click", () => signOut(""));
  $("menu-toggle").addEventListener("click", () => setMenu(!$("rail").classList.contains("is-open")));

  $("org-select").addEventListener("change", (event) => {
    const value = event.target.value;
    if (value && value !== state.org) {
      chooseOrganization(value);
    }
  });

  $("new-org-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("new-org-id");
    const id = String(input.value || "").trim();
    if (!id) {
      return;
    }
    const submit = event.target.querySelector("[type=submit]");
    run(submit, async () => {
      const created = await api("POST", ADMIN_API + "/organizations", { id: id });
      input.value = "";
      await loadOrganizations(created && created.id ? created.id : id);
    });
  });

  $("tabs").addEventListener("wa-tab-show", (event) => {
    if (event.target === $("tabs") && event.detail && event.detail.name !== state.tab) {
      setTab(event.detail.name);
    }
  });

  document.querySelectorAll("[data-refresh]").forEach((node) => {
    node.addEventListener("click", () => loadTab());
  });

  $("issue-client").addEventListener("click", (event) => issueCredential(event.currentTarget, "client"));
  $("issue-admin").addEventListener("click", (event) => issueCredential(event.currentTarget, "org_admin"));
  $("issued-copy").addEventListener("wa-copy", () => {
    $("issued-copied").hidden = false;
  });
  $("issued-copy").addEventListener("wa-error", (event) => {
    // Icons inside the copy button raise their own (composed) wa-error; only the
    // copy button's own event means the copy failed.
    if (event.composedPath()[0] !== event.currentTarget) {
      return;
    }
    showError("Copy failed. Select the token and copy it manually.");
  });
  $("issued-close").addEventListener("click", tearOffIssued);
  $("jobs-more").addEventListener("click", (event) => loadMoreJobs(event.currentTarget));
  wireTestbench();

  $("confirm-ok").addEventListener("click", () => closeConfirmation(true));
  $("confirm-cancel").addEventListener("click", () => closeConfirmation(false));
  $("confirm").addEventListener("wa-hide", (event) => {
    if (event.target === $("confirm") && settleConfirmation) {
      settleConfirmation(false);
    }
  });

  state.token = loadToken();
  if (state.token) {
    enter();
  } else {
    showLogin("");
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
