// Resona hub management page. ES module: importing the vendored Carbon bundle registers
// every cds-* element before this module's body runs, and yields the icon descriptors.
import {
  Add16,
  ArrowRight16,
  ChartBar20,
  Chemistry20,
  Chip20,
  Dashboard20,
  Enterprise20,
  List20,
  Logout20,
  OverflowMenuVertical16,
  Password20,
  Renew16,
  Settings20,
  Upload16,
  UserAvatar20,
} from "/admin/vendor/carbon/carbon.js";

const TOKEN_KEY = "resona-admin-token";
const ADMIN_API = "/v1/admin";
const TESTBENCH_API = "/v1/testbench";
const SETTINGS_API = "/v1/settings";
const POLL_MS = 5000;
const ITEMS_PAGE = 50;
const MAX_RUN_CANDIDATES = 16;
const DESKTOP_MIN_PX = 1056;
const SVG_NS = "http://www.w3.org/2000/svg";

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
const ITEM_STATUSES = {
  awaiting_draft: { label: "Awaiting draft", type: "cool-gray" },
  needs_correction: { label: "Needs correction", type: "purple" },
  corrected: { label: "Corrected", type: "green" },
};
// Job and run states share one colour scheme.
const STATUS_TAGS = {
  pending: { label: "Pending", type: "purple" },
  queued: { label: "Queued", type: "purple" },
  processing: { label: "Processing", type: "blue" },
  completed: { label: "Completed", type: "green" },
  failed: { label: "Failed", type: "red" },
  cancelled: { label: "Cancelled", type: "warm-gray" },
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

const SECTIONS = [
  { name: "overview", label: "Overview", icon: Dashboard20 },
  { name: "workers", label: "Workers", icon: Chip20 },
  { name: "credentials", label: "Credentials", icon: Password20 },
  { name: "jobs", label: "Jobs", icon: List20 },
  { name: "settings", label: "Settings", icon: Settings20 },
  { name: "baselines", label: "Baselines", icon: ChartBar20 },
  { name: "testbench", label: "Testbench", icon: Chemistry20 },
];
const SECTION_NAMES = SECTIONS.map((section) => section.name);

const state = {
  token: null,
  me: null,
  org: null,
  section: "overview",
  jobsCursor: null,
  // Bumped whenever the view context changes; responses for an older context are dropped.
  seq: 0,
  // Bumped whenever the selected organization changes.
  orgSeq: 0,
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
  // Drained even for 204: an unread body leaves the request to be cancelled.
  const text = await response.text();
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

// ── DOM helpers ────────────────────────────────────────────────────────────

// Creates an element: `class` sets className, `on<event>` functions become listeners,
// `true` sets a boolean attribute and false/null/undefined skip the attribute.
function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([name, value]) => {
    if (value === undefined || value === null || value === false) {
      return;
    }
    if (name === "class") {
      node.className = value;
    } else if (name.startsWith("on") && typeof value === "function") {
      node.addEventListener(name.slice(2), value);
    } else {
      node.setAttribute(name, value === true ? "" : String(value));
    }
  });
  append(node, children);
  return node;
}

function append(node, children) {
  children.flat(Infinity).forEach((child) => {
    if (child === undefined || child === null || child === false || child === "") {
      return;
    }
    node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  });
  return node;
}

// Renders a @carbon/icons descriptor ({elem, attrs, content}) without innerHTML.
function icon(descriptor, attrs) {
  const build = (desc) => {
    const node = document.createElementNS(SVG_NS, desc.elem);
    Object.entries(desc.attrs || {}).forEach(([name, value]) => node.setAttribute(name, String(value)));
    (desc.content || []).forEach((child) => node.appendChild(build(child)));
    return node;
  };
  const svg = build(descriptor);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  Object.entries(attrs || {}).forEach(([name, value]) => svg.setAttribute(name, String(value)));
  return svg;
}

function isBlank(value) {
  return value === undefined || value === null || value === "";
}

function muted(text) {
  return h("span", { class: "muted" }, text);
}

function dash() {
  return muted("—");
}

function mono(text) {
  return isBlank(text) ? dash() : h("span", { class: "mono" }, String(text));
}

function num(text) {
  return isBlank(text) ? dash() : h("span", { class: "num" }, String(text));
}

function srOnly(text) {
  return h("span", { class: "sr-only" }, text);
}

function tag(text, type, title) {
  return h("cds-tag", { type: type, size: "sm", title: title }, text);
}

function statusTag(status) {
  const known = STATUS_TAGS[status];
  return known ? tag(known.label, known.type) : tag(String(status), "gray");
}

// An identifier you can copy by clicking it.
function copyable(text, label) {
  if (isBlank(text)) {
    return dash();
  }
  return h(
    "cds-code-snippet",
    { type: "inline", feedback: "Copied", "tooltip-content": label || "Copy to clipboard" },
    String(text)
  );
}

function button(label, options) {
  const opts = options || {};
  const node = h("cds-button", {
    kind: opts.kind || "primary",
    size: opts.size,
    "tooltip-text": opts.iconOnly ? label : null,
    "tooltip-position": opts.iconOnly ? "bottom" : null,
    disabled: opts.disabled,
  });
  if (opts.icon) {
    node.appendChild(icon(opts.icon, { slot: "icon" }));
  }
  if (!opts.iconOnly) {
    node.appendChild(document.createTextNode(label));
  }
  if (opts.onClick) {
    node.addEventListener("click", (event) => {
      if (!node.disabled) {
        opts.onClick(event);
      }
    });
  }
  return node;
}

// Label/value pairs for expanded rows and detail blocks.
function facts(pairs) {
  const list = h("dl", { class: "facts" });
  pairs.forEach(([label, value]) => {
    if (value === undefined) {
      return;
    }
    const dd = h("dd");
    if (value instanceof Node) {
      dd.appendChild(value);
    } else if (isBlank(value)) {
      dd.appendChild(dash());
    } else {
      dd.textContent = String(value);
    }
    list.appendChild(h("div", null, h("dt", null, label), dd));
  });
  return list;
}

function whenReady(node, callback) {
  customElements
    .whenDefined(node.localName)
    .then(() => node.updateComplete)
    .then(() => callback(node));
}

// ── Time and numbers ───────────────────────────────────────────────────────

const DATE_FORMAT = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const RELATIVE_FORMAT = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const RELATIVE_UNITS = [
  ["year", 31536000],
  ["month", 2592000],
  ["week", 604800],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
  ["second", 1],
];

function absoluteText(iso) {
  const time = Date.parse(iso);
  return Number.isNaN(time) ? String(iso) : DATE_FORMAT.format(time);
}

function relativeText(iso) {
  const time = Date.parse(iso);
  if (Number.isNaN(time)) {
    return String(iso);
  }
  const seconds = (time - Date.now()) / 1000;
  const size = Math.abs(seconds);
  for (const [unit, length] of RELATIVE_UNITS) {
    if (size >= length || unit === "second") {
      return RELATIVE_FORMAT.format(Math.round(seconds / length), unit);
    }
  }
  return "";
}

function durationText(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (seconds < 60) {
    return seconds + " s";
  }
  if (minutes < 60) {
    return minutes + " min";
  }
  if (hours < 48) {
    return hours + " h " + (minutes % 60) + " min";
  }
  return days + " d " + (hours % 24) + " h";
}

function waitedSince(iso) {
  const created = Date.parse(iso);
  return Number.isNaN(created) ? null : Date.now() - created;
}

// Relative time in a table cell; the exact timestamp is in the title.
function whenCell(iso) {
  if (!iso) {
    return dash();
  }
  return h("span", { title: absoluteText(iso) + " (" + iso + ")" }, relativeText(iso));
}

// Absolute time with its distance from now, for expanded rows.
function whenFull(iso) {
  if (!iso) {
    return dash();
  }
  return h("span", { title: iso }, absoluteText(iso), " ", muted("(" + relativeText(iso) + ")"));
}

function formatBytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size)) {
    return "";
  }
  const units = ["B", "kB", "MB", "GB"];
  let index = 0;
  let scaled = size;
  while (scaled >= 1000 && index < units.length - 1) {
    scaled /= 1000;
    index += 1;
  }
  return (index === 0 ? String(scaled) : scaled.toFixed(1)) + " " + units[index];
}

function percent(value) {
  return isBlank(value) ? "" : (Number(value) * 100).toFixed(1) + " %";
}

// ── Notifications ──────────────────────────────────────────────────────────

function showError(error) {
  const code = error instanceof ApiError ? error.code : "error";
  const message = error && error.message ? error.message : String(error);
  // Before sign-in completes the error belongs to the sign-in form.
  if ($("app").hidden) {
    showLoginMessage(message);
    return;
  }
  const banner = $("banner");
  banner.setAttribute("title", code);
  banner.setAttribute("subtitle", message);
  banner.hidden = false;
  banner.open = true;
}

function hideBanner() {
  const banner = $("banner");
  banner.hidden = true;
  banner.setAttribute("title", "");
  banner.setAttribute("subtitle", "");
}

function report(error) {
  if (error && error.handled) {
    return;
  }
  showError(error instanceof ApiError ? error : new ApiError(0, "error", error && error.message ? error.message : String(error)));
}

function toast(title, subtitle, kind) {
  const note = h("cds-toast-notification", {
    kind: kind || "success",
    title: title,
    subtitle: subtitle || "",
    timeout: 6000,
    "low-contrast": true,
  });
  note.addEventListener("cds-notification-closed", () => note.remove());
  $("toasts").appendChild(note);
}

// Runs an action triggered by a control: disables it while pending and reports errors.
async function run(trigger, action) {
  if (trigger) {
    trigger.disabled = true;
  }
  hideBanner();
  try {
    await action();
  } catch (error) {
    report(error);
  } finally {
    if (trigger && trigger.isConnected) {
      trigger.disabled = false;
    }
  }
}

// ── Modals ─────────────────────────────────────────────────────────────────

let settleConfirmation = null;

function confirmAction(options) {
  if (settleConfirmation) {
    settleConfirmation(false);
  }
  $("confirm-title").textContent = options.title;
  $("confirm-text").textContent = options.message;
  const ok = $("confirm-ok");
  ok.textContent = options.confirmLabel;
  ok.setAttribute("kind", options.danger === false ? "primary" : "danger");
  $("confirm-cancel").textContent = options.dismissLabel || "Go back";
  $("confirm").open = true;
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

function showIssued(issued) {
  $("issued-role").textContent = (ROLE_LABELS[issued.role] || issued.role) + " token";
  $("issued-id").textContent = issued.id;
  $("issued-token").replaceChildren(
    h(
      "cds-code-snippet",
      { type: "single", feedback: "Copied", "tooltip-content": "Copy token", "aria-label": "Issued token" },
      issued.token
    )
  );
  $("issued").open = true;
}

// Done: the token leaves the DOM and nothing keeps a reference to it.
function clearIssued() {
  $("issued-token").replaceChildren();
  $("issued-id").textContent = "";
  $("issued-role").textContent = "";
  $("issued").open = false;
}

// ── Data tables ────────────────────────────────────────────────────────────

// A Carbon data table driven by an array of rows. Sorting and the toolbar search act on
// the loaded rows; the table's own DOM-based sort and filter are bypassed so a re-render
// keeps the chosen order and filter.
//
// cfg: {title, description, size, columns: [{key, header, render(row), sort?(row),
// secondary?}], rowId(row), expand?(row), actions?(row) -> [{label, danger, disabled,
// onClick(menu)}], actionsLabel?, toolbar?: [nodes], onRefresh?, empty: string|nodes}
function dataTable(cfg) {
  const size = cfg.size || "md";
  const view = { rows: [], sort: null, query: "", expanded: new Set(), loading: false };
  const columnCount = cfg.columns.length + (cfg.actions ? 1 : 0);

  const table = h("cds-table", { size: size, expandable: Boolean(cfg.expand) });
  table.appendChild(h("cds-table-header-title", { slot: "title" }, cfg.title));
  if (cfg.description) {
    table.appendChild(h("cds-table-header-description", { slot: "description" }, cfg.description));
  }

  const search = h("cds-table-toolbar-search", {
    persistent: true,
    placeholder: "Filter loaded rows",
    "label-text": "Filter " + cfg.title.toLowerCase(),
    size: size === "md" ? "lg" : size,
  });
  search.addEventListener("cds-search-input", (event) => {
    event.stopPropagation();
    view.query = String((event.detail && event.detail.value) || "").trim();
    applyFilter();
  });
  const toolButtonSize = size === "md" ? "lg" : size;
  const tools = [search];
  if (cfg.onRefresh) {
    tools.push(button("Refresh", { kind: "ghost", icon: Renew16, iconOnly: true, size: toolButtonSize, onClick: cfg.onRefresh }));
  }
  (cfg.toolbar || []).forEach((node) => {
    if (node.localName === "cds-button") {
      node.setAttribute("size", toolButtonSize);
    }
    tools.push(node);
  });
  table.appendChild(h("cds-table-toolbar", { slot: "toolbar" }, h("cds-table-toolbar-content", null, tools)));

  const headerRow = h("cds-table-header-row");
  cfg.columns.forEach((column) => {
    const cell = h(
      "cds-table-header-cell",
      {
        class: column.secondary ? "col-secondary" : null,
        "is-sortable": Boolean(column.sort),
        "sort-direction": column.sort ? "none" : null,
      },
      column.header
    );
    cell.dataset.key = column.key;
    headerRow.appendChild(cell);
  });
  if (cfg.actions) {
    headerRow.appendChild(h("cds-table-header-cell", { class: "col-actions" }, srOnly("Actions")));
  }
  headerRow.addEventListener("cds-table-header-cell-sort", (event) => {
    event.stopPropagation();
    const cell = event.target;
    const direction = event.detail && event.detail.sortDirection;
    headerRow.querySelectorAll("cds-table-header-cell[is-sortable]").forEach((other) => {
      if (other !== cell) {
        other.setAttribute("sort-direction", "none");
      }
    });
    view.sort = !direction || direction === "none" ? null : { key: cell.dataset.key, direction: direction };
    render();
  });

  const body = h("cds-table-body");
  table.appendChild(h("cds-table-head", null, headerRow));
  table.appendChild(body);
  table.addEventListener("cds-table-row-expando-toggled", (event) => {
    const id = event.target && event.target.dataset ? event.target.dataset.id : undefined;
    if (id === undefined) {
      return;
    }
    if (event.detail && event.detail.expanded) {
      view.expanded.add(id);
    } else {
      view.expanded.delete(id);
    }
  });

  const skeleton = h("cds-table-skeleton", {
    "row-count": 3,
    "column-count": columnCount,
    "show-header": true,
    "show-toolbar": true,
    hidden: true,
  });
  skeleton.headers = cfg.columns.map((column) => column.header).concat(cfg.actions ? [""] : []);
  const empty = h("p", { class: "table-empty", hidden: true });
  const footer = h("div", { class: "table-footer", hidden: true });
  const root = h("div", { class: "table-block" }, table, skeleton, empty, footer);

  function compare(a, b) {
    if (typeof a === "number" && typeof b === "number") {
      return a - b;
    }
    return String(a ?? "").localeCompare(String(b ?? ""), undefined, { numeric: true });
  }

  function render() {
    const rows = view.rows.slice();
    if (view.sort) {
      const column = cfg.columns.find((entry) => entry.key === view.sort.key);
      const factor = view.sort.direction === "descending" ? -1 : 1;
      if (column && column.sort) {
        rows.sort((a, b) => factor * compare(column.sort(a), column.sort(b)));
      }
    }
    const nodes = [];
    rows.forEach((row) => {
      const id = String(cfg.rowId(row));
      const tr = h("cds-table-row");
      tr.dataset.id = id;
      if (cfg.rowClass) {
        const extra = cfg.rowClass(row);
        if (extra) {
          tr.className = extra;
        }
      }
      cfg.columns.forEach((column) => {
        const content = column.render(row);
        tr.appendChild(
          h("cds-table-cell", { class: column.secondary ? "col-secondary" : null }, isBlank(content) ? dash() : content)
        );
      });
      if (cfg.actions) {
        const label = cfg.actionsLabel ? cfg.actionsLabel(row) : "Actions";
        tr.appendChild(h("cds-table-cell", { class: "col-actions" }, overflowMenu(label, cfg.actions(row), size)));
      }
      nodes.push(tr);
      if (cfg.expand) {
        const detail = h("cds-table-expanded-row", { colspan: columnCount + 1 }, h("div", { class: "row-detail" }, cfg.expand(row)));
        nodes.push(detail);
        if (view.expanded.has(id)) {
          tr.expanded = true;
          detail.expanded = true;
        }
      }
    });
    body.replaceChildren(...nodes);
    applyFilter();
  }

  function applyFilter() {
    const query = view.query.toLowerCase();
    let visible = 0;
    body.querySelectorAll("cds-table-row").forEach((tr) => {
      const detail = tr.nextElementSibling && tr.nextElementSibling.localName === "cds-table-expanded-row" ? tr.nextElementSibling : null;
      const text = (tr.textContent + " " + (detail ? detail.textContent : "")).toLowerCase();
      const filtered = Boolean(query) && !text.includes(query);
      tr.filtered = filtered;
      if (detail) {
        detail.filtered = filtered;
      }
      if (!filtered) {
        visible += 1;
      }
    });
    updateEmpty(visible);
  }

  function updateEmpty(visible) {
    const showSkeleton = view.loading && view.rows.length === 0;
    table.hidden = showSkeleton;
    skeleton.hidden = !showSkeleton;
    table.setAttribute("aria-busy", view.loading ? "true" : "false");
    if (showSkeleton) {
      empty.hidden = true;
      return;
    }
    if (view.rows.length === 0) {
      empty.replaceChildren();
      append(empty, [cfg.empty]);
      empty.hidden = false;
    } else if (visible === 0) {
      empty.textContent = "No loaded row matches “" + view.query + "”.";
      empty.hidden = false;
    } else {
      empty.hidden = true;
    }
  }

  return {
    root: root,
    table: table,
    footer: footer,
    get rows() {
      return view.rows;
    },
    setRows(rows) {
      view.rows = rows.slice();
      const ids = new Set(view.rows.map((row) => String(cfg.rowId(row))));
      Array.from(view.expanded).forEach((id) => {
        if (!ids.has(id)) {
          view.expanded.delete(id);
        }
      });
      render();
    },
    rerender: render,
    setLoading(on) {
      view.loading = on;
      applyFilter();
    },
    clear() {
      view.rows = [];
      view.expanded.clear();
      body.replaceChildren();
      footer.hidden = true;
      applyFilter();
    },
    rowElement(id) {
      return body.querySelector('cds-table-row[data-id="' + CSS.escape(String(id)) + '"]');
    },
  };
}

// Per-row actions in a Carbon overflow menu. Destructive items are `danger`; an action
// that is not allowed stays visible, disabled, with the reason in its label.
function overflowMenu(label, items, size) {
  const menu = h(
    "cds-overflow-menu",
    { size: size === "md" ? "md" : "sm", flipped: true, "aria-label": label },
    icon(OverflowMenuVertical16, { slot: "icon", class: "cds--overflow-menu__icon" }),
    h("span", { slot: "tooltip-content" }, label)
  );
  const body = h("cds-overflow-menu-body", { flipped: true });
  items.forEach((item) => {
    const entry = h(
      "cds-overflow-menu-item",
      { danger: Boolean(item.danger) && !item.disabled, disabled: Boolean(item.disabled), divider: Boolean(item.divider) },
      item.disabled && item.reason ? item.label + " (" + item.reason + ")" : item.label
    );
    entry.addEventListener("click", () => {
      if (!item.disabled) {
        menu.open = false;
        item.onClick(menu);
      }
    });
    body.appendChild(entry);
  });
  menu.appendChild(body);
  return menu;
}

// ── Capabilities ───────────────────────────────────────────────────────────

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

function capabilityKey(candidate) {
  return JSON.stringify(
    ["engine", "model", "model_revision", "accelerator", "ocr_engine"].map((field) =>
      candidate && typeof candidate === "object" ? candidate[field] ?? null : candidate
    )
  );
}

// One entry per capability; colour carries the state and a visually hidden suffix
// says it in words: green = approved and reported, gray = reported but not approved,
// magenta = approved but no longer reported.
const CAPABILITY_STATES = {
  approved: { type: "green", text: "Approved and reported" },
  reported: { type: "cool-gray", text: "Reported, not approved" },
  stale: { type: "magenta", text: "Approved, no longer reported" },
};

function capabilityEntries(worker) {
  const reported = worker.reported_capabilities || [];
  const approved = worker.approved_capabilities || [];
  const approvedKeys = new Set(approved.map(capabilityKey));
  const reportedKeys = new Set(reported.map(capabilityKey));
  return reported
    .map((candidate) => [candidate, approvedKeys.has(capabilityKey(candidate)) ? "approved" : "reported"])
    .concat(approved.filter((candidate) => !reportedKeys.has(capabilityKey(candidate))).map((c) => [c, "stale"]));
}

function capabilityList(worker) {
  const entries = capabilityEntries(worker);
  if (!entries.length) {
    return h("p", { class: "muted" }, "No capabilities reported or approved.");
  }
  const legend = h(
    "p",
    { class: "cap-legend", "aria-hidden": "true" },
    Object.values(CAPABILITY_STATES).map((entry) => tag(entry.text, entry.type))
  );
  const list = h("ul", { class: "cap-list", "aria-label": "Capabilities" });
  entries.forEach(([candidate, kind]) => {
    const meta = CAPABILITY_STATES[kind];
    list.appendChild(
      h(
        "li",
        null,
        h("cds-tag", { type: meta.type, size: "sm", title: meta.text }, h("span", { class: "mono" }, formatCandidate(candidate)), srOnly(" (" + meta.text.toLowerCase() + ")"))
      )
    );
  });
  return h("div", null, legend, list);
}

function capabilitySummary(worker) {
  const counts = { approved: 0, reported: 0, stale: 0 };
  capabilityEntries(worker).forEach(([, kind]) => {
    counts[kind] += 1;
  });
  const parts = [];
  if (counts.approved) {
    parts.push(counts.approved + " approved");
  }
  if (counts.reported) {
    parts.push(counts.reported + " not approved");
  }
  if (counts.stale) {
    parts.push(counts.stale + " no longer reported");
  }
  return parts.length ? parts.join(" · ") : "None";
}

// ── Session ────────────────────────────────────────────────────────────────

function showLoginMessage(message) {
  const note = $("login-message");
  note.setAttribute("subtitle", message || "");
  note.hidden = !message;
  if (message) {
    note.open = true;
  }
}

function showLogin(message) {
  $("app").hidden = true;
  $("login").hidden = false;
  showLoginMessage(message);
  whenReady($("login-token"), (input) => input.focus());
}

function closePanels() {
  ["org-panel", "account-panel"].forEach((id) => $(id).removeAttribute("expanded"));
  ["org-action", "account-action"].forEach((id) => {
    $(id).active = false;
  });
}

function signOut(message) {
  state.seq += 1;
  state.orgSeq += 1;
  state.token = null;
  state.me = null;
  state.org = null;
  clearToken();
  closeConfirmation(false);
  clearIssued();
  $("new-org-modal").open = false;
  $("contribute-modal").open = false;
  closePanels();
  resetViews();
  $("org-switcher").replaceChildren();
  setOrgLabels("");
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
    showLogin(error.message);
    return;
  }
  state.me = me;
  const role = ROLE_LABELS[me.role] || me.role;
  $("me-role").textContent = role;
  $("me-org").textContent = me.organization_id;
  $("me-credential").textContent = me.credential_id || "";
  $("account-action").setAttribute("tooltip-text", "Signed in: " + role);
  $("account-action").setAttribute("aria-label", "Signed in: " + role);
  const operator = me.role === "hub_operator";
  $("org-action").hidden = !operator;
  $("new-org").hidden = !operator;
  $("login").hidden = true;
  showLoginMessage("");
  $("app").hidden = false;
  try {
    await loadOrganizations(null);
  } catch (error) {
    report(error);
  }
}

function submitLogin() {
  const input = $("login-token");
  const token = String(input.value || "").trim();
  input.value = "";
  if (!token) {
    showLoginMessage("Paste a token to sign in.");
    return;
  }
  hideBanner();
  showLoginMessage("");
  state.token = token;
  saveToken(token);
  enter();
}

// ── Organizations ──────────────────────────────────────────────────────────

async function loadOrganizations(preferred) {
  const data = await api("GET", ADMIN_API + "/organizations");
  const items = (data && data.items) || [];
  const ids = items.map((item) => item.id);
  const home = state.me.organization_id;
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
  state.orgIds = ids;
  selectOrganization(chosen);
}

function renderOrgSwitcher() {
  const switcher = $("org-switcher");
  switcher.replaceChildren();
  const home = state.me ? state.me.organization_id : null;
  (state.orgIds || []).forEach((id) => {
    const item = h("cds-switcher-item", { href: "#/" + state.section, selected: id === state.org }, id, id === home ? " (home)" : "");
    item.addEventListener("click", (event) => {
      event.preventDefault();
      closePanels();
      chooseOrganization(id);
    });
    switcher.appendChild(item);
  });
}

function setOrgLabels(orgId) {
  document.querySelectorAll(".org-name").forEach((node) => {
    node.textContent = orgId || "";
  });
  $("header-org").textContent = orgId || "";
}

function chooseOrganization(orgId) {
  if (orgId === state.org) {
    loadSection();
    return;
  }
  selectOrganization(orgId);
  toast("Organization switched", "Now showing " + orgId + ".", "info");
}

function selectOrganization(orgId) {
  state.org = orgId;
  state.orgSeq += 1;
  resetViews();
  setOrgLabels(orgId || "No organization");
  renderOrgSwitcher();
  route();
}

async function createOrganization(trigger) {
  const input = $("new-org-id");
  const id = String(input.value || "").trim();
  if (!id) {
    input.setAttribute("invalid", "");
    input.setAttribute("invalid-text", "Enter an organization ID.");
    return;
  }
  input.removeAttribute("invalid");
  await run(trigger, async () => {
    const created = await api("POST", ADMIN_API + "/organizations", { id: id });
    input.value = "";
    $("new-org-modal").open = false;
    const newId = created && created.id ? created.id : id;
    await loadOrganizations(newId);
    toast("Organization created", newId + " is selected.");
  });
}

// ── Navigation ─────────────────────────────────────────────────────────────

function buildNavigation() {
  const items = $("side-nav-items");
  SECTIONS.forEach((section) => {
    const link = h("cds-side-nav-link", { href: "#/" + section.name }, section.label);
    link.dataset.section = section.name;
    link.prepend(icon(section.icon, { slot: "title-icon", width: 16, height: 16 }));
    link.addEventListener("click", () => {
      if (window.innerWidth < DESKTOP_MIN_PX) {
        $("side-nav").expanded = false;
      }
    });
    items.appendChild(link);
  });
  $("org-action").appendChild(icon(Enterprise20, { slot: "icon" }));
  $("account-action").appendChild(icon(UserAvatar20, { slot: "icon" }));
  $("sign-out").appendChild(icon(Logout20, { slot: "icon" }));
}

function sectionFromHash() {
  const match = /^#\/([a-z]+)$/.exec(window.location.hash);
  return match && SECTION_NAMES.includes(match[1]) ? match[1] : null;
}

function route() {
  let name = sectionFromHash();
  if (!name) {
    name = "overview";
    window.history.replaceState(null, "", "#/overview");
  }
  showSection(name);
}

function showSection(name) {
  state.section = name;
  SECTION_NAMES.forEach((section) => {
    $("section-" + section).hidden = section !== name;
  });
  document.querySelectorAll("cds-side-nav-link").forEach((link) => {
    link.active = link.dataset.section === name;
  });
  document.querySelectorAll("cds-switcher-item").forEach((item) => {
    item.setAttribute("href", "#/" + name);
  });
  const label = SECTIONS.find((section) => section.name === name).label;
  document.title = label + " · Resona hub";
  syncPolling();
  loadSection();
}

const loaders = {
  overview: loadOverview,
  workers: loadWorkers,
  credentials: loadCredentials,
  jobs: (seq) => loadJobs(seq, false),
  settings: loadSettings,
  baselines: loadBaselines,
  testbench: loadTestbench,
};

function loadSection() {
  state.seq += 1;
  const seq = state.seq;
  if (!state.token || !state.org) {
    return;
  }
  hideBanner();
  loaders[state.section](seq).catch(report);
}

// Marks tables as loading for the duration of `work` unless a newer load took over.
async function withLoading(seq, tables, work) {
  tables.forEach((table) => table.setLoading(true));
  try {
    await work();
  } finally {
    if (seq === state.seq) {
      tables.forEach((table) => table.setLoading(false));
    }
  }
}

function resetViews() {
  [workersTable, credentialsTable, jobsTable, baselinesTable, releasesTable, itemsTable, runsTable, lbAudioTable, lbDocumentTable].forEach(
    (table) => table.clear()
  );
  $("overview-tiles").replaceChildren();
  $("overview-attention").replaceChildren();
  $("settings-keys").replaceChildren();
  $("settings-json").replaceChildren();
  $("settings-body").hidden = true;
  $("settings-revision").hidden = true;
  state.jobsCursor = null;
  resetTestbench();
}

// ── Overview ───────────────────────────────────────────────────────────────

function workerLabel(worker) {
  return worker.name ? "“" + worker.name + "”" : worker.id;
}

function tile(href, label, value, detail) {
  return h(
    "cds-clickable-tile",
    { href: href, class: "stat-tile" },
    h("span", { class: "stat-label" }, label),
    h("span", { class: "stat-value num" }, value),
    h("span", { class: "stat-detail" }, detail),
    icon(ArrowRight16, { class: "stat-arrow" })
  );
}

async function loadOverview(seq) {
  $("overview-tiles").replaceChildren(
    ...[1, 2, 3, 4].map(() => h("div", { class: "stat-tile stat-skeleton" }, h("cds-skeleton-text", { lines: 3, paragraph: true })))
  );
  $("overview-attention").replaceChildren(h("cds-skeleton-text", { lines: 3, paragraph: true }));
  const [workers, credentials, jobs, settings] = await Promise.allSettled([
    api("GET", orgPath() + "/workers"),
    api("GET", orgPath() + "/credentials"),
    api("GET", orgPath() + "/jobs"),
    api("GET", orgPath() + "/settings"),
  ]);
  if (seq !== state.seq) {
    return;
  }
  const failed = [workers, credentials, jobs, settings].find((result) => result.status === "rejected");
  if (failed) {
    report(failed.reason);
  }
  const value = (result) => (result.status === "fulfilled" ? result.value || {} : null);
  renderOverview(value(workers), value(credentials), value(jobs), value(settings));
}

function renderOverview(workersData, credentialsData, jobsData, settingsData) {
  const tiles = [];
  const workers = workersData ? workersData.items || [] : null;
  const jobs = jobsData ? jobsData.items || [] : null;

  if (workers) {
    const active = workers.filter((worker) => !worker.revoked);
    const online = active.filter((worker) => worker.online).length;
    const approved = active.filter((worker) => worker.approved).length;
    tiles.push(tile("#/workers", "Workers online", String(online), approved + " approved · " + active.length + " not revoked"));
  } else {
    tiles.push(tile("#/workers", "Workers online", "–", "Could not load workers"));
  }

  if (credentialsData) {
    const live = (credentialsData.items || []).filter((credential) => !credential.revoked_at).length;
    tiles.push(tile("#/credentials", "Live credentials", String(live), "Not revoked, every role"));
  } else {
    tiles.push(tile("#/credentials", "Live credentials", "–", "Could not load credentials"));
  }

  if (jobs) {
    const pending = jobs.filter((job) => job.status === "pending");
    const more = jobsData.next_cursor ? "+" : "";
    const oldest = pending.reduce((max, job) => Math.max(max, waitedSince(job.created_at) || 0), 0);
    const detail = pending.length ? "Oldest waiting " + durationText(oldest) : "None waiting";
    tiles.push(tile("#/jobs", "Pending jobs", pending.length + more, detail + (more ? " (first page)" : "")));
  } else {
    tiles.push(tile("#/jobs", "Pending jobs", "–", "Could not load jobs"));
  }

  if (settingsData) {
    tiles.push(tile("#/settings", "Settings revision", String(settingsData.revision), "Applies to new jobs"));
  } else {
    tiles.push(tile("#/settings", "Settings revision", "–", "Could not load settings"));
  }
  $("overview-tiles").replaceChildren(...tiles);

  const findings = [];
  (workers || [])
    .filter((worker) => !worker.revoked)
    .forEach((worker) => {
      const entries = capabilityEntries(worker);
      const reportedOnly = entries.filter(([, kind]) => kind === "reported").length;
      const stale = entries.filter(([, kind]) => kind === "stale").length;
      if (!worker.approved && (worker.reported_capabilities || []).length) {
        findings.push(["Workers", "Worker " + workerLabel(worker) + " reports " + worker.reported_capabilities.length + " capabilities and is not approved.", "#/workers"]);
      } else if (worker.approved && reportedOnly) {
        findings.push(["Workers", "Worker " + workerLabel(worker) + " reports " + reportedOnly + " capabilities that are not approved.", "#/workers"]);
      }
      if (worker.approved && !worker.online) {
        findings.push([
          "Workers",
          "Worker " + workerLabel(worker) + " is approved but offline" + (worker.last_seen ? ", last seen " + relativeText(worker.last_seen) + "." : ", never seen."),
          "#/workers",
        ]);
      }
      if (stale) {
        findings.push(["Workers", "Worker " + workerLabel(worker) + " has " + stale + " approved capabilities it no longer reports.", "#/workers"]);
      }
    });
  (jobs || [])
    .filter((job) => job.status === "pending" && job.queue_reason)
    .forEach((job) => {
      const waited = waitedSince(job.created_at);
      findings.push([
        "Jobs",
        "Job " + job.job_id + " is pending: " + (QUEUE_REASONS[job.queue_reason] || job.queue_reason) + (waited !== null ? " (waiting " + durationText(waited) + ")." : "."),
        "#/jobs",
      ]);
    });

  const box = $("overview-attention");
  if (!findings.length) {
    box.replaceChildren(h("p", { class: "empty-note" }, workers && jobs ? "Nothing needs attention." : "Some data could not be loaded, so this list may be incomplete."));
    return;
  }
  const list = h("cds-structured-list", { condensed: true });
  list.appendChild(
    h(
      "cds-structured-list-head",
      null,
      h(
        "cds-structured-list-header-row",
        null,
        h("cds-structured-list-header-cell", null, "Section"),
        h("cds-structured-list-header-cell", null, "Finding"),
        h("cds-structured-list-header-cell", null, srOnly("Go to section"))
      )
    )
  );
  const listBody = h("cds-structured-list-body");
  findings.forEach(([area, text, href]) => {
    listBody.appendChild(
      h(
        "cds-structured-list-row",
        null,
        h("cds-structured-list-cell", null, area),
        h("cds-structured-list-cell", null, text),
        h("cds-structured-list-cell", null, h("cds-link", { href: href }, "Open " + area.toLowerCase()))
      )
    );
  });
  list.appendChild(listBody);
  box.replaceChildren(list);
}

// ── Workers ────────────────────────────────────────────────────────────────

function workerStatusTag(worker) {
  if (worker.revoked) {
    return tag("Revoked", "red");
  }
  return worker.online ? tag("Online", "green") : tag("Offline", "gray");
}

function workerStatusRank(worker) {
  return worker.revoked ? 2 : worker.online ? 0 : 1;
}

function workerApproval(worker) {
  if (!worker.approved) {
    return tag("Not approved", "cool-gray");
  }
  return tag(worker.approved_private ? "Approved private" : "Approved non-private", "green");
}

function workerPath(worker) {
  return orgPath() + "/workers/" + encodeURIComponent(worker.id);
}

function workerActions(worker) {
  const reported = (worker.reported_capabilities || []).length > 0;
  const blocked = worker.revoked ? "worker revoked" : !reported ? "nothing reported" : null;
  return [
    {
      label: "Approve private",
      disabled: Boolean(blocked),
      reason: blocked,
      onClick: (menu) => approveWorker(menu, worker, true),
    },
    {
      label: "Approve non-private",
      disabled: Boolean(blocked),
      reason: blocked,
      onClick: (menu) => approveWorker(menu, worker, false),
    },
    {
      label: "Revoke worker",
      danger: true,
      divider: true,
      disabled: worker.revoked,
      reason: worker.revoked ? "already revoked" : null,
      onClick: (menu) => revokeWorker(menu, worker),
    },
  ];
}

function approveWorker(trigger, worker, isPrivate) {
  return run(trigger, async () => {
    await api("POST", workerPath(worker) + "/approve", { private: isPrivate });
    toast("Worker approved", workerLabel(worker) + " now runs " + (isPrivate ? "private" : "non-private") + " jobs with its reported capabilities.");
    loadSection();
  });
}

async function revokeWorker(trigger, worker) {
  const confirmed = await confirmAction({
    title: "Revoke worker " + workerLabel(worker) + "?",
    message: "Its token stops working immediately and it claims no further jobs. This cannot be undone; the machine must be enrolled again.",
    confirmLabel: "Revoke worker",
    dismissLabel: "Keep worker",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    await api("POST", workerPath(worker) + "/revoke");
    toast("Worker revoked", workerLabel(worker) + " can no longer connect.");
    loadSection();
  });
}

function workerDetail(worker) {
  return h(
    "div",
    { class: "detail-stack" },
    facts([
      ["Worker ID", copyable(worker.id, "Copy worker ID")],
      ["Runtime", mono(worker.runtime_version)],
      ["Last seen", worker.last_seen ? whenFull(worker.last_seen) : "Never seen"],
      ["Approval", workerApproval(worker)],
    ]),
    h("h3", { class: "detail-title" }, "Capabilities"),
    capabilityList(worker)
  );
}

const workersTable = dataTable({
  title: "Workers",
  description: "Pull workers enrolled in this organization. Expand a row for its capabilities.",
  columns: [
    { key: "name", header: "Name", sort: (w) => w.name || "", render: (w) => (w.name ? w.name : muted("Unnamed")) },
    { key: "id", header: "ID", secondary: true, sort: (w) => w.id, render: (w) => mono(w.id) },
    { key: "status", header: "Status", sort: workerStatusRank, render: workerStatusTag },
    { key: "seen", header: "Last seen", secondary: true, sort: (w) => w.last_seen || "", render: (w) => (w.last_seen ? whenCell(w.last_seen) : muted("Never")) },
    { key: "approval", header: "Approval", sort: (w) => (w.approved ? (w.approved_private ? 0 : 1) : 2), render: workerApproval },
    { key: "caps", header: "Capabilities", secondary: true, render: capabilitySummary },
    { key: "runtime", header: "Runtime", secondary: true, sort: (w) => w.runtime_version || "", render: (w) => mono(w.runtime_version) },
  ],
  rowId: (w) => w.id,
  expand: workerDetail,
  actions: workerActions,
  actionsLabel: (w) => "Actions for worker " + (w.name || w.id),
  onRefresh: () => loadSection(),
  empty: ["No workers have enrolled in this organization. Enroll one on the hub host with ", h("span", { class: "mono" }, "resona-admin worker enroll"), "."],
});

async function loadWorkers(seq) {
  await withLoading(seq, [workersTable], async () => {
    const data = await api("GET", orgPath() + "/workers");
    if (seq !== state.seq) {
      return;
    }
    workersTable.setRows((data && data.items) || []);
  });
}

// ── Credentials ────────────────────────────────────────────────────────────

function isOwnCredential(credential) {
  return Boolean(state.me && credential.id === state.me.credential_id);
}

function credentialState(credential) {
  const nodes = [credential.revoked_at ? tag("Revoked", "red") : tag("Live", "green")];
  if (isOwnCredential(credential)) {
    nodes.push(tag("This session", "blue"));
  }
  return h("span", { class: "tag-row" }, nodes);
}

function credentialActions(credential) {
  let reason = null;
  if (credential.revoked_at) {
    reason = "already revoked";
  } else if (credential.role === "worker") {
    reason = "revoke the worker instead";
  } else if (isOwnCredential(credential)) {
    reason = "you are signed in with it";
  }
  return [
    {
      label: "Revoke token",
      danger: true,
      disabled: Boolean(reason),
      reason: reason,
      onClick: (menu) => revokeCredential(menu, credential),
    },
  ];
}

function credentialDetail(credential) {
  let note = null;
  if (!credential.revoked_at && credential.role === "worker") {
    note = "Worker tokens are revoked together with their worker on the Workers page.";
  } else if (!credential.revoked_at && isOwnCredential(credential)) {
    note = "You are signed in with this credential, so it cannot be revoked from this session.";
  }
  return h(
    "div",
    { class: "detail-stack" },
    facts([
      ["Credential ID", copyable(credential.id, "Copy credential ID")],
      ["Role", ROLE_LABELS[credential.role] || credential.role],
      ["Worker", credential.worker_id ? copyable(credential.worker_id, "Copy worker ID") : ""],
      ["Issued", whenFull(credential.created_at)],
      ["Revoked", credential.revoked_at ? whenFull(credential.revoked_at) : "Not revoked"],
    ]),
    note ? h("p", { class: "muted" }, note) : null
  );
}

const credentialsTable = dataTable({
  title: "Credentials",
  description: "Tokens of this organization. Only a hash is stored; a token is shown once, at issue.",
  columns: [
    { key: "id", header: "Credential", sort: (c) => c.id, render: (c) => mono(c.id) },
    { key: "role", header: "Role", sort: (c) => ROLE_LABELS[c.role] || c.role, render: (c) => ROLE_LABELS[c.role] || c.role },
    { key: "state", header: "State", sort: (c) => (c.revoked_at ? 1 : 0), render: credentialState },
    { key: "worker", header: "Worker", secondary: true, sort: (c) => c.worker_id || "", render: (c) => mono(c.worker_id) },
    { key: "issued", header: "Issued", secondary: true, sort: (c) => c.created_at || "", render: (c) => whenCell(c.created_at) },
  ],
  rowId: (c) => c.id,
  rowClass: (c) => (c.revoked_at ? "row-revoked" : null),
  expand: credentialDetail,
  actions: credentialActions,
  actionsLabel: (c) => "Actions for credential " + c.id,
  onRefresh: () => loadSection(),
  toolbar: [
    button("Issue admin token", { kind: "tertiary", onClick: (event) => issueCredential(event.currentTarget, "org_admin") }),
    button("Issue client token", { kind: "primary", icon: Add16, onClick: (event) => issueCredential(event.currentTarget, "client") }),
  ],
  empty: "No credentials. Issue a client or admin token with the buttons above.",
});

async function loadCredentials(seq) {
  await withLoading(seq, [credentialsTable], async () => {
    const data = await api("GET", orgPath() + "/credentials");
    if (seq !== state.seq) {
      return;
    }
    credentialsTable.setRows((data && data.items) || []);
  });
}

async function revokeCredential(trigger, credential) {
  const confirmed = await confirmAction({
    title: "Revoke credential " + credential.id + "?",
    message: "Anything using this " + (ROLE_LABELS[credential.role] || credential.role).toLowerCase() + " token stops working immediately. This cannot be undone.",
    confirmLabel: "Revoke token",
    dismissLabel: "Keep token",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    await api("POST", orgPath() + "/credentials/" + encodeURIComponent(credential.id) + "/revoke");
    toast("Token revoked", "Credential " + credential.id + " no longer authenticates.");
    loadSection();
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
    if (state.section === "credentials") {
      loadSection();
    }
  });
}

// ── Jobs ───────────────────────────────────────────────────────────────────

function canManageJobs() {
  return Boolean(state.me && state.me.role === "org_admin" && state.org === state.me.organization_id);
}

function jobWaiting(job) {
  if (job.status !== "pending") {
    return job.status === "processing" && !isBlank(job.attempt) ? "Attempt " + job.attempt : "";
  }
  const waited = waitedSince(job.created_at);
  const reason = job.queue_reason ? QUEUE_REASONS[job.queue_reason] || job.queue_reason : null;
  return h(
    "span",
    { class: "cell-stack" },
    waited !== null ? h("span", { class: "num" }, durationText(waited)) : null,
    reason ? h("span", { class: "muted", title: job.queue_reason }, reason) : null
  );
}

function jobDetail(job) {
  const error = job.error ? h("span", { class: "cell-stack" }, mono(job.error.code), h("span", null, String(job.error.message))) : "None";
  return h(
    "div",
    { class: "detail-stack" },
    facts([
      ["Job ID", copyable(String(job.job_id), "Copy job ID")],
      ["Status", statusTag(job.status)],
      ["Queue reason", job.queue_reason ? (QUEUE_REASONS[job.queue_reason] || job.queue_reason) + " (" + job.queue_reason + ")" : "None"],
      ["Attempt", isBlank(job.attempt) ? "" : num(job.attempt)],
      ["Privacy", job.private ? "Private" : "Non-private"],
      ["Execution profile", mono(job.execution_profile)],
      ["Candidate", job.candidate ? mono(formatCandidate(job.candidate)) : "Not assigned"],
      ["Error", error],
      ["Result", job.result_available ? tag("Available", "green") : "Not available"],
      ["Created", whenFull(job.created_at)],
      ["Updated", whenFull(job.updated_at)],
      ["Deadline", whenFull(job.deadline_at)],
    ])
  );
}

function jobActions(job) {
  const active = job.status === "pending" || job.status === "processing";
  return [
    {
      label: "Cancel job",
      danger: true,
      disabled: !active,
      reason: active ? null : "already " + job.status,
      onClick: (menu) => cancelJob(menu, job),
    },
    { label: "Delete job", danger: true, divider: true, onClick: (menu) => deleteJob(menu, job) },
  ];
}

function jobsTableConfig(withActions) {
  return {
    title: "Jobs",
    description: withActions
      ? "Newest first. Expand a row for its timeline, error and result."
      : "Newest first, metadata only. Cancel and delete are open to this organization's own admins.",
    columns: [
      { key: "id", header: "Job", sort: (j) => Number(j.job_id), render: (j) => mono(j.job_id) },
      { key: "kind", header: "Kind", sort: (j) => j.kind, render: (j) => j.kind },
      { key: "status", header: "Status", sort: (j) => j.status, render: (j) => statusTag(j.status) },
      { key: "waiting", header: "Waiting", sort: (j) => (j.status === "pending" ? waitedSince(j.created_at) || 0 : -1), render: jobWaiting },
      { key: "profile", header: "Execution profile", secondary: true, sort: (j) => j.execution_profile || "", render: (j) => mono(j.execution_profile) },
      { key: "privacy", header: "Privacy", secondary: true, sort: (j) => (j.private ? 0 : 1), render: (j) => (j.private ? "Private" : "Non-private") },
      { key: "result", header: "Result", secondary: true, sort: (j) => (j.result_available ? 0 : 1), render: (j) => (j.result_available ? tag("Available", "green") : "") },
      { key: "created", header: "Created", secondary: true, sort: (j) => j.created_at || "", render: (j) => whenCell(j.created_at) },
    ],
    rowId: (j) => j.job_id,
    expand: jobDetail,
    actions: withActions ? jobActions : null,
    actionsLabel: (j) => "Actions for job " + j.job_id,
    onRefresh: () => loadSection(),
    empty: "No jobs in this organization yet. Jobs appear once a client submits audio or a document.",
  };
}

// Two configurations of one table: with row actions (own org admin) or metadata only.
let jobsTable = null;
let jobsTableActions = null;
const jobsMore = button("Load more", { kind: "tertiary", size: "sm", onClick: (event) => loadMoreJobs(event.currentTarget) });

function mountJobsTable(withActions) {
  if (jobsTable && jobsTableActions === withActions) {
    return;
  }
  jobsTable = dataTable(jobsTableConfig(withActions));
  jobsTableActions = withActions;
  jobsTable.footer.append(jobsMore, h("span", { class: "muted", id: "jobs-count" }));
  $("jobs-table").replaceChildren(jobsTable.root);
}

async function loadJobs(seq, appendPage) {
  mountJobsTable(canManageJobs());
  let path = orgPath() + "/jobs";
  if (appendPage && state.jobsCursor) {
    path += "?cursor=" + encodeURIComponent(state.jobsCursor);
  }
  const work = async () => {
    const data = await api("GET", path);
    if (seq !== state.seq) {
      return;
    }
    const items = (data && data.items) || [];
    jobsTable.setRows(appendPage ? jobsTable.rows.concat(items) : items);
    state.jobsCursor = (data && data.next_cursor) || null;
    renderJobsFooter();
  };
  if (appendPage) {
    await work();
  } else {
    await withLoading(seq, [jobsTable], work);
  }
}

function renderJobsFooter() {
  const count = jobsTable.rows.length;
  jobsTable.footer.hidden = count === 0;
  jobsMore.hidden = !state.jobsCursor;
  $("jobs-count").textContent = count + (count === 1 ? " job loaded" : " jobs loaded") + (state.jobsCursor ? "; more on the hub" : "");
}

function loadMoreJobs(trigger) {
  const seq = state.seq;
  return run(trigger, () => loadJobs(seq, true));
}

function jobPath(job) {
  return "/v1/jobs/" + encodeURIComponent(job.job_id);
}

async function cancelJob(trigger, job) {
  const confirmed = await confirmAction({
    title: "Cancel job " + job.job_id + "?",
    message: "A pending job is removed from the queue; a processing job is stopped on its worker. This cannot be undone.",
    confirmLabel: "Cancel job",
    dismissLabel: "Keep job",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    const updated = await api("POST", jobPath(job) + "/cancel");
    if (updated && updated.job_id !== undefined) {
      jobsTable.setRows(jobsTable.rows.map((row) => (row.job_id === updated.job_id ? updated : row)));
    } else {
      loadSection();
    }
    toast("Job cancelled", "Job " + job.job_id + " is " + (updated && updated.status ? updated.status : "cancelled") + ".");
  });
}

async function deleteJob(trigger, job) {
  const confirmed = await confirmAction({
    title: "Delete job " + job.job_id + "?",
    message: "Its upload and result are removed from the hub, and an active job is cancelled first. This cannot be undone.",
    confirmLabel: "Delete job",
    dismissLabel: "Keep job",
  });
  if (!confirmed) {
    return;
  }
  await run(trigger, async () => {
    await api("DELETE", jobPath(job));
    jobsTable.setRows(jobsTable.rows.filter((row) => row.job_id !== job.job_id));
    renderJobsFooter();
    toast("Job deleted", "Job " + job.job_id + " and its files are gone.");
  });
}

// ── Settings ───────────────────────────────────────────────────────────────

function settingSummary(value) {
  if (Array.isArray(value)) {
    return value.length ? "List of " + value.length + (value.length === 1 ? " entry" : " entries") : "Empty list";
  }
  if (value && typeof value === "object") {
    const keys = Object.keys(value);
    return keys.length ? keys.length + (keys.length === 1 ? " key: " : " keys: ") + keys.join(", ") : "Empty object";
  }
  return value === null ? "null" : String(value);
}

async function loadSettings(seq) {
  $("settings-skeleton").hidden = false;
  try {
    const settings = await api("GET", orgPath() + "/settings");
    if (seq !== state.seq) {
      return;
    }
    const values = (settings && settings.settings) || {};
    const revision = $("settings-revision");
    revision.textContent = "Revision " + settings.revision;
    revision.hidden = false;

    const list = h("cds-structured-list", { condensed: true });
    list.appendChild(
      h(
        "cds-structured-list-head",
        null,
        h("cds-structured-list-header-row", null, h("cds-structured-list-header-cell", null, "Key"), h("cds-structured-list-header-cell", null, "Value"))
      )
    );
    const listBody = h("cds-structured-list-body");
    Object.keys(values).forEach((key) => {
      const value = values[key];
      const scalar = value === null || typeof value !== "object";
      listBody.appendChild(
        h(
          "cds-structured-list-row",
          null,
          h("cds-structured-list-cell", null, h("span", { class: "mono" }, key)),
          h("cds-structured-list-cell", null, h("span", { class: scalar ? "mono" : null }, settingSummary(value)))
        )
      );
    });
    list.appendChild(listBody);
    $("settings-keys").replaceChildren(list);
    $("settings-json").replaceChildren(
      h(
        "cds-code-snippet",
        { type: "multi", feedback: "Copied", "tooltip-content": "Copy settings JSON", "show-more-text": "Show more", "show-less-text": "Show less" },
        JSON.stringify(values, null, 2)
      )
    );
    $("settings-body").hidden = false;
  } finally {
    if (seq === state.seq) {
      $("settings-skeleton").hidden = true;
    }
  }
}

// ── Baselines ──────────────────────────────────────────────────────────────

function shortDigest(digest) {
  return isBlank(digest) ? dash() : h("span", { class: "mono", title: String(digest) }, String(digest).slice(0, 12) + "…");
}

const baselinesTable = dataTable({
  title: "Baselines",
  description: "Highest promoted revision per execution profile and platform.",
  columns: [
    { key: "profile", header: "Execution profile", sort: (b) => b.execution_profile, render: (b) => mono(b.execution_profile) },
    { key: "platform", header: "Platform", sort: (b) => b.platform, render: (b) => mono(b.platform) },
    { key: "revision", header: "Revision", sort: (b) => Number(b.revision), render: (b) => num(b.revision) },
    { key: "release", header: "Release", secondary: true, sort: (b) => b.release_id || "", render: (b) => mono(b.release_id) },
    { key: "digest", header: "Corpus SHA-256", secondary: true, render: (b) => shortDigest(b.corpus_sha256) },
    { key: "created", header: "Created", secondary: true, sort: (b) => b.created_at || "", render: (b) => whenCell(b.created_at) },
  ],
  rowId: (b) => b.execution_profile + "/" + b.platform,
  expand: (b) =>
    h(
      "div",
      { class: "detail-stack" },
      facts([
        ["Evaluation", copyable(b.evaluation_id, "Copy evaluation ID")],
        ["Release", copyable(b.release_id, "Copy release ID")],
        ["Created", whenFull(b.created_at)],
      ]),
      h("h3", { class: "detail-title" }, "Corpus SHA-256"),
      isBlank(b.corpus_sha256)
        ? dash()
        : h("cds-code-snippet", { type: "single", feedback: "Copied", "tooltip-content": "Copy SHA-256" }, String(b.corpus_sha256))
    ),
  onRefresh: () => loadSection(),
  empty: [
    "No baselines promoted yet. Record an evaluation with ",
    h("span", { class: "mono" }, "resona eval --remote"),
    " and promote it with ",
    h("span", { class: "mono" }, "resona eval promote"),
    ".",
  ],
});

const releasesTable = dataTable({
  title: "Approved releases",
  description: "Worker releases approved for production jobs by a promoted evaluation.",
  size: "sm",
  columns: [
    { key: "profile", header: "Execution profile", sort: (r) => r.execution_profile, render: (r) => mono(r.execution_profile) },
    { key: "platform", header: "Platform", sort: (r) => r.platform, render: (r) => mono(r.platform) },
    { key: "release", header: "Release", sort: (r) => r.release_id || "", render: (r) => mono(r.release_id) },
    { key: "evaluation", header: "Evaluation", secondary: true, sort: (r) => r.evaluation_id || "", render: (r) => mono(r.evaluation_id) },
    { key: "approved", header: "Approved", secondary: true, sort: (r) => r.approved_at || "", render: (r) => whenCell(r.approved_at) },
  ],
  rowId: (r) => [r.execution_profile, r.platform, r.release_id].join("/"),
  onRefresh: () => loadSection(),
  empty: "No approved releases. Promoting an evaluation approves its release for that execution profile and platform.",
});

async function loadBaselines(seq) {
  await withLoading(seq, [baselinesTable, releasesTable], async () => {
    const data = await api("GET", orgPath() + "/baselines");
    if (seq !== state.seq) {
      return;
    }
    baselinesTable.setRows((data && data.items) || []);
    releasesTable.setRows((data && data.approved_releases) || []);
  });
}

// ── Testbench ──────────────────────────────────────────────────────────────

const tb = {
  cursor: null,
  // Bumped per item-list load; older responses are dropped.
  listSeq: 0,
  created: null,
  file: null,
  // The open item; null when none is open.
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

// Runs a testbench action; a switched-off testbench (403) sends the section back to its status.
function tbAction(trigger, action) {
  return run(trigger, async () => {
    try {
      await action();
    } catch (error) {
      if (error && error.code === "testbench_disabled") {
        loadSection();
      }
      throw error;
    }
  });
}

function showTestbenchView(mode, message) {
  const forbidden = $("tb-forbidden");
  forbidden.hidden = mode !== "forbidden";
  forbidden.setAttribute("subtitle", mode === "forbidden" ? message || "" : "");
  $("tb-content").hidden = mode === "forbidden" || mode === "none";
  $("tb-off").hidden = mode !== "off";
  $("tb-main").hidden = mode !== "on";
  $("tb-disable").hidden = mode !== "on";
}

function renderTestbenchStatus(status) {
  $("tb-state").replaceChildren(status.enabled ? tag("Enabled", "green") : tag("Disabled", "gray"));
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
  const work = [loadItems(seq, false), loadLeaderboard(seq)];
  if (tb.openId !== null) {
    work.push(refreshDetail());
  }
  await Promise.all(work);
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

async function setTestbenchEnabled(trigger, enabled) {
  const confirmed = await confirmAction(
    enabled
      ? {
          title: "Enable the testbench for " + state.org + "?",
          message: "Recordings and scans contributed to the testbench are stored on the hub persistently, until you delete the item.",
          confirmLabel: "Enable testbench",
          dismissLabel: "Keep it off",
          danger: false,
        }
      : {
          title: "Disable the testbench for " + state.org + "?",
          message: "Existing items, references and runs are kept on the hub, but nothing can be added or read until the testbench is enabled again.",
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
        loadSection();
        throw new ApiError(
          409,
          "revision_conflict",
          "The settings changed in the meantime, so nothing was changed. The status below is up to date; try again."
        );
      }
      throw error;
    }
    toast(enabled ? "Testbench enabled" : "Testbench disabled", "Settings saved as a new revision.");
    loadSection();
  });
}

function resetTestbench() {
  closeDetail();
  tb.cursor = null;
  tb.listSeq += 1;
  tb.created = null;
  $("tb-created").hidden = true;
  $("tb-state").replaceChildren();
  ["items", "corrected", "runs"].forEach((name) => {
    $("tb-count-" + name).textContent = "–";
  });
  $("tb-lb-corrected").textContent = "";
  itemsMore.hidden = true;
  selectTestbenchTab("items");
  showTestbenchView("none");
}

function selectTestbenchTab(value) {
  $("tb-tabs").value = value;
}

// ── Testbench: contribute and item list ────────────────────────────────────

function openContribute() {
  tb.file = null;
  $("tb-uploader").querySelectorAll("cds-file-uploader-item").forEach((node) => node.remove());
  $("tb-label").value = "";
  $("contribute-modal").open = true;
}

function setContributeFile(file) {
  tb.file = file || null;
  const uploader = $("tb-uploader");
  uploader.querySelectorAll("cds-file-uploader-item").forEach((node) => node.remove());
  if (file) {
    const item = h("cds-file-uploader-item", { state: "edit", "icon-description": "Remove file" }, file.name);
    item.addEventListener("cds-file-uploader-item-deleted", () => {
      tb.file = null;
      item.remove();
    });
    uploader.appendChild(item);
  }
}

function contribute(trigger) {
  const file = tb.file;
  if (!file) {
    showError(new ApiError(0, "no_file", "Choose a recording or scan to contribute."));
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
  return tbAction(trigger, async () => {
    const item = await apiUpload(TESTBENCH_API + "/items", form);
    if (orgSeq !== state.orgSeq || !item) {
      return;
    }
    $("contribute-modal").open = false;
    setContributeFile(null);
    $("tb-label").value = "";
    tb.created = item;
    const created = $("tb-created");
    created.setAttribute(
      "subtitle",
      "Item " + item.id + " (" + item.kind + ", " + item.language + (item.label ? ", “" + item.label + "”" : "") + "). Its draft run is queued."
    );
    created.replaceChildren(button("Open item", { kind: "ghost", size: "sm", onClick: (event) => openItem(item.id, event.currentTarget) }));
    created.hidden = false;
    created.open = true;
    loadSection();
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

async function loadItems(seq, appendPage) {
  const listSeq = (tb.listSeq += 1);
  const params = itemFilters();
  if (appendPage && tb.cursor) {
    params.set("cursor", tb.cursor);
  }
  const work = async () => {
    const data = await api("GET", TESTBENCH_API + "/items?" + params.toString());
    if (seq !== state.seq || listSeq !== tb.listSeq) {
      return;
    }
    const items = (data && data.items) || [];
    itemsTable.setRows(appendPage ? itemsTable.rows.concat(items) : items);
    tb.cursor = (data && data.next_cursor) || null;
    itemsMore.hidden = !tb.cursor;
    itemsTable.footer.hidden = !tb.cursor;
  };
  if (appendPage) {
    await work();
  } else {
    await withLoading(seq, [itemsTable], work);
  }
}

function reloadItems() {
  tb.cursor = null;
  loadItems(state.seq, false).catch(report);
}

function itemStatus(item) {
  const known = ITEM_STATUSES[item.status];
  return h(
    "span",
    { class: "cell-stack" },
    known ? tag(known.label, known.type) : tag(String(item.status), "gray"),
    item.draft_status === "failed" ? h("span", { class: "muted" }, "Draft run failed") : null
  );
}

function itemLink(item) {
  return h(
    "cds-link",
    {
      href: "#/testbench",
      onclick: (event) => {
        event.preventDefault();
        openItem(item.id, null);
      },
    },
    "Item " + item.id
  );
}

const itemsTable = dataTable({
  title: "Items",
  description: "Contributed recordings and scans. Open one to correct its reference and run candidates.",
  columns: [
    { key: "id", header: "Item", sort: (i) => Number(i.id), render: itemLink },
    { key: "status", header: "Status", sort: (i) => i.status, render: itemStatus },
    { key: "kind", header: "Kind", sort: (i) => i.kind, render: (i) => i.kind },
    { key: "language", header: "Language", secondary: true, sort: (i) => i.language, render: (i) => mono(i.language) },
    { key: "label", header: "Label", secondary: true, sort: (i) => i.label || "", render: (i) => i.label || "" },
    { key: "size", header: "Size", secondary: true, sort: (i) => Number(i.size) || 0, render: (i) => (isBlank(i.size) ? "" : num(formatBytes(i.size))) },
    { key: "runs", header: "Runs", sort: (i) => Number(i.run_count) || 0, render: (i) => num(i.run_count) },
    { key: "created", header: "Created", secondary: true, sort: (i) => i.created_at || "", render: (i) => whenCell(i.created_at) },
  ],
  rowId: (i) => i.id,
  rowClass: (i) => (i.id === tb.openId ? "row-selected" : null),
  actions: (item) => [
    { label: "Open item", onClick: (menu) => openItem(item.id, menu) },
    { label: "Delete item", danger: true, divider: true, onClick: (menu) => deleteItem(menu, item.id) },
  ],
  actionsLabel: (i) => "Actions for item " + i.id,
  onRefresh: () => loadSection(),
  toolbar: [button("Contribute", { kind: "primary", icon: Upload16, onClick: openContribute })],
  empty: "No items match. Contribute a recording or scan with the Contribute button.",
});
const itemsMore = button("Load more", {
  kind: "tertiary",
  size: "sm",
  onClick: (event) => {
    const seq = state.seq;
    tbAction(event.currentTarget, () => loadItems(seq, true));
  },
});
itemsTable.footer.appendChild(itemsMore);

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
  itemsTable.rerender();
  $("tb-diff").hidden = true;
  runsTable.clear();
  $("tb-detail-title").textContent = "Item " + id;
  $("tb-detail-meta").textContent = "";
  $("tb-reference").value = "";
  $("tb-reference-note").textContent = "";
  $("tb-candidates").replaceChildren();
  $("tb-candidates-empty").hidden = true;
  updateRunButton();
  updateSaveButton();
  $("tb-detail-none").hidden = true;
  $("tb-detail").hidden = false;
  $("tb-tab-runs").textContent = "Runs · item " + id;
  selectTestbenchTab("runs");
  await tbAction(trigger, async () => {
    $("tb-detail-spinner").hidden = false;
    runsTable.setLoading(true);
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
        runsTable.setLoading(false);
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
  $("tb-detail-none").hidden = false;
  $("tb-tab-runs").textContent = "Runs";
  $("tb-diff").hidden = true;
  $("tb-diff-text").replaceChildren();
  $("tb-candidates").replaceChildren();
  runsTable.clear();
  itemsTable.rerender();
}

function itemGone() {
  closeDetail();
  showError(new ApiError(404, "not_found", "That item no longer exists."));
  loadSection();
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

function runIsActive(entry) {
  return entry.status === "queued" || entry.status === "processing";
}

function detailSignature(detail) {
  return [detail.status, detail.reference_set_at]
    .concat((detail.runs || []).map((entry) => [entry.id, entry.status, entry.wer, entry.cer].join(":")))
    .join("|");
}

function renderDetail(detail, initial) {
  $("tb-detail-title").textContent = "Item " + detail.id;
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

  if (itemsTable.rows.some((row) => row.id === detail.id)) {
    itemsTable.setRows(itemsTable.rows.map((row) => (row.id === detail.id ? Object.assign({}, row, detail) : row)));
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
    note = "Saved reference" + (detail.reference_set_at ? " (" + absoluteText(detail.reference_set_at) + ")" : "") + ". Saving again re-scores every completed run.";
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
  runsTable.setRows(runs);

  const signature = detailSignature(detail);
  if (!initial && tb.runSig !== null && signature !== tb.runSig) {
    refreshAggregates();
  }
  tb.runSig = signature;

  if (tb.diffRunId !== null) {
    const open = runs.find((entry) => entry.id === tb.diffRunId);
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
    toast("Reference saved", "Item " + id + ": completed runs are re-scored.");
  }).then(updateSaveButton);
}

async function deleteItem(trigger, id) {
  if (id === null || id === undefined) {
    return;
  }
  const confirmed = await confirmAction({
    title: "Delete item " + id + "?",
    message: "Its stored original, reference and every run are removed, and active runs are cancelled. This cannot be undone.",
    confirmLabel: "Delete item",
    dismissLabel: "Keep item",
  });
  if (!confirmed) {
    return;
  }
  await tbAction(trigger, async () => {
    await api("DELETE", itemPath(id));
    if (id === tb.openId) {
      closeDetail();
      selectTestbenchTab("items");
    }
    toast("Item deleted", "Item " + id + " and its runs are gone.");
    loadSection();
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
  $("tb-preview").replaceChildren(h("p", { class: "muted" }, text));
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
    node = h("audio", { class: "original-audio" });
    node.controls = true;
    node.preload = "metadata";
    node.addEventListener("error", () => previewNote("This browser cannot play the original. Use Download original."));
  } else {
    node = h("img", { class: "original-image", alt: "Original scan of item " + detail.id });
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
      "No " + (kind || "") + " candidate is available. Approve a private worker on the Workers page; candidates appear once it has reported them as tested.";
    empty.hidden = false;
    updateRunButton();
    return;
  }
  empty.hidden = true;
  const group = h("fieldset", { class: "candidate-group" }, h("legend", { class: "detail-title" }, kind + " candidates"));
  matching.forEach((entry) => {
    const key = capabilityKey(entry.candidate);
    const online = Number(entry.workers_online) || 0;
    const box = h("cds-checkbox", { checked: tb.selected.has(key) }, formatCandidate(entry.candidate));
    box.addEventListener("cds-checkbox-changed", (event) => {
      if (event.detail && event.detail.checked) {
        tb.selected.add(key);
      } else {
        tb.selected.delete(key);
      }
      updateRunButton();
    });
    group.appendChild(
      h("div", { class: "candidate-row" }, box, tag(online + (online === 1 ? " worker online" : " workers online"), online ? "green" : "gray"))
    );
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
  let queuedNote = "";
  return tbAction(trigger, async () => {
    const created = await api("POST", itemPath(id) + "/runs", { candidates: chosen });
    if (id !== tb.openId) {
      return;
    }
    const queued = created && created.items ? created.items.length : 0;
    tb.selected.clear();
    renderCandidates();
    const skipped = chosen.length - queued;
    queuedNote = "Queued " + queued + (queued === 1 ? " run" : " runs") + (skipped > 0 ? "; " + skipped + " already running was skipped" : "");
    await refreshDetail();
  }).then(() => {
    updateRunButton();
    if (queuedNote && !tb.selected.size) {
      $("tb-run-count").textContent = queuedNote;
    }
  });
}

const runsTable = dataTable({
  title: "Runs",
  description: "Draft and bench runs of this item. Open a run's word diff from its menu.",
  size: "sm",
  columns: [
    {
      key: "id",
      header: "Run",
      sort: (r) => r.id,
      render: (r) =>
        h(
          "cds-link",
          {
            href: "#/testbench",
            onclick: (event) => {
              event.preventDefault();
              showDiff(r.id, true);
            },
          },
          "Run " + r.id
        ),
    },
    {
      key: "role",
      header: "Role",
      sort: (r) => r.role,
      render: (r) =>
        r.role === "draft"
          ? tag("Draft", "gray", "The reference started from this output, so the draft favours itself and is left out of the leaderboard.")
          : tag("Bench", "blue"),
    },
    { key: "candidate", header: "Candidate", sort: (r) => formatCandidate(r.candidate), render: (r) => mono(formatCandidate(r.candidate)) },
    { key: "device", header: "Device", secondary: true, sort: (r) => r.device || "", render: (r) => mono(r.device) },
    { key: "seconds", header: "Seconds", secondary: true, sort: (r) => (isBlank(r.seconds) ? Infinity : Number(r.seconds)), render: (r) => (isBlank(r.seconds) ? "" : num(Number(r.seconds).toFixed(1))) },
    { key: "wer", header: "WER", sort: (r) => (isBlank(r.wer) ? Infinity : Number(r.wer)), render: (r) => num(percent(r.wer)) },
    { key: "cer", header: "CER", secondary: true, sort: (r) => (isBlank(r.cer) ? Infinity : Number(r.cer)), render: (r) => num(percent(r.cer)) },
    {
      key: "status",
      header: "Status",
      sort: (r) => r.status,
      render: (r) =>
        h(
          "span",
          { class: "cell-stack" },
          statusTag(r.status),
          r.queue_reason ? h("span", { class: "muted" }, QUEUE_REASONS[r.queue_reason] || r.queue_reason) : null,
          r.error_code ? mono(r.error_code) : null
        ),
    },
  ],
  rowId: (r) => r.id,
  rowClass: (r) => (r.id === tb.diffRunId ? "row-selected" : null),
  actions: (entry) => [
    { label: "Show word diff", onClick: () => showDiff(entry.id, true) },
    { label: "Delete run", danger: true, divider: true, onClick: (menu) => deleteRun(menu, entry) },
  ],
  actionsLabel: (r) => "Actions for run " + r.id,
  onRefresh: () => tbAction(null, refreshDetail),
  empty: "No runs yet. Select candidates above and run them.",
});

async function deleteRun(trigger, entry) {
  const id = tb.openId;
  const confirmed = await confirmAction({
    title: "Delete run " + entry.id + "?",
    message: formatCandidate(entry.candidate) + ": the run and its scores are removed; a queued or processing run is cancelled.",
    confirmLabel: "Delete run",
    dismissLabel: "Keep run",
  });
  if (!confirmed || id !== tb.openId) {
    return;
  }
  await tbAction(trigger, async () => {
    await api("DELETE", itemPath(id) + "/runs/" + encodeURIComponent(entry.id));
    if (id !== tb.openId) {
      return;
    }
    if (tb.diffRunId === entry.id) {
      hideDiff();
    }
    toast("Run deleted", "Run " + entry.id + " is gone.");
    await refreshDetail();
  });
}

// While a run is queued or processing and the item is open, the detail refreshes every
// POLL_MS. Polling stops on close, in another section and while the page is hidden.
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
    state.section !== "testbench" ||
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
  runsTable.rerender();
}

async function showDiff(runId, reveal) {
  const id = tb.openId;
  if (id === null) {
    return;
  }
  tb.diffRunId = runId;
  const seq = (tb.diffSeq += 1);
  const summary = tb.detail && (tb.detail.runs || []).find((entry) => entry.id === runId);
  if (summary) {
    tb.diffSig = [summary.status, summary.wer, summary.cer, tb.detail.reference_set_at].join(":");
  }
  runsTable.rerender();
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

function renderDiff(entry) {
  $("tb-diff-title").textContent = "Word diff · run " + entry.id;
  const text = $("tb-diff-text");
  text.replaceChildren();
  const warnings = $("tb-diff-warnings");
  warnings.replaceChildren();
  (entry.warnings || []).forEach((warning) => warnings.appendChild(h("li", null, String(warning))));

  const summary = [formatCandidate(entry.candidate)];
  if (!isBlank(entry.wer)) {
    summary.push("WER " + percent(entry.wer));
  }
  if (!isBlank(entry.cer)) {
    summary.push("CER " + percent(entry.cer));
  }
  if (entry.role === "draft") {
    summary.push("draft run: biased towards the reference");
  }

  const alignment = entry.alignment;
  if (!Array.isArray(alignment)) {
    $("tb-diff-summary").textContent = summary.join(" · ");
    const hasReference = tb.detail && tb.detail.reference !== null && tb.detail.reference !== undefined;
    diffNote(
      entry.status !== "completed"
        ? "No word diff yet: this run is " + entry.status + "."
        : !hasReference
          ? "No word diff yet: save a reference first."
          : "This run has no alignment."
    );
    return;
  }
  diffNote("");
  const counts = { equal: 0, substitute: 0, insert: 0, delete: 0 };
  const fragment = document.createDocumentFragment();
  const word = (value) => (value === null || value === undefined ? "" : String(value));
  alignment.forEach((part, index) => {
    if (index > 0) {
      fragment.appendChild(document.createTextNode(" "));
    }
    if (counts[part.op] !== undefined) {
      counts[part.op] += 1;
    }
    switch (part.op) {
      case "substitute":
        fragment.appendChild(
          h("span", { class: "diff-substitute" }, h("del", { class: "diff-ref" }, word(part.ref)), " ", h("ins", { class: "diff-hyp" }, word(part.hyp)))
        );
        break;
      case "insert":
        fragment.appendChild(h("ins", { class: "diff-hyp" }, word(part.hyp)));
        break;
      case "delete":
        fragment.appendChild(h("del", { class: "diff-ref" }, word(part.ref)));
        break;
      default:
        fragment.appendChild(h("span", { class: "diff-equal" }, part.ref !== null && part.ref !== undefined ? word(part.ref) : word(part.hyp)));
    }
  });
  text.appendChild(fragment);
  summary.push(counts.equal + " equal, " + counts.substitute + " replaced, " + counts.insert + " added, " + counts.delete + " missing");
  $("tb-diff-summary").textContent = summary.join(" · ");
}

// ── Testbench: leaderboard ─────────────────────────────────────────────────

function leaderboardTable(kind, title, empty) {
  let best = {};
  const metric = (field, format) => (entry) => {
    const value = entry[field];
    if (isBlank(value)) {
      return dash();
    }
    const node = h("span", { class: "cell-inline" }, num(format(Number(value))));
    if (best[field] !== null && best[field] !== undefined && Number(value) === best[field]) {
      node.appendChild(tag("Best", "green", "Best in column"));
    }
    return node;
  };
  const sortable = (field) => (entry) => (isBlank(entry[field]) ? Infinity : Number(entry[field]));
  const table = dataTable({
    title: title,
    size: "sm",
    columns: [
      { key: "candidate", header: "Candidate", sort: (e) => formatCandidate(e.candidate), render: (e) => mono(formatCandidate(e.candidate)) },
      { key: "items", header: "Items (n)", sort: (e) => Number(e.items) || 0, render: (e) => num(e.items) },
      { key: "wer_mean", header: "WER mean", sort: sortable("wer_mean"), render: metric("wer_mean", (v) => (v * 100).toFixed(1) + " %") },
      { key: "cer_mean", header: "CER mean", sort: sortable("cer_mean"), render: metric("cer_mean", (v) => (v * 100).toFixed(1) + " %") },
      { key: "seconds_mean", header: "Mean seconds", secondary: true, sort: sortable("seconds_mean"), render: metric("seconds_mean", (v) => v.toFixed(1)) },
    ],
    rowId: (e) => capabilityKey(e.candidate),
    onRefresh: () => {
      const seq = state.seq;
      loadLeaderboard(seq).catch(report);
    },
    empty: empty,
  });
  table.kind = kind;
  table.setEntries = (rows) => {
    const bestOf = (field) => {
      const values = rows.map((entry) => entry[field]).filter((value) => !isBlank(value)).map(Number);
      return rows.length > 1 && values.length ? Math.min(...values) : null;
    };
    best = { wer_mean: bestOf("wer_mean"), cer_mean: bestOf("cer_mean"), seconds_mean: bestOf("seconds_mean") };
    table.setRows(rows);
  };
  return table;
}

const lbAudioTable = leaderboardTable("audio", "Audio", "No completed bench runs on corrected audio items yet.");
const lbDocumentTable = leaderboardTable("document", "Documents", "No completed bench runs on corrected document items yet.");

async function loadLeaderboard(seq) {
  const data = await api("GET", TESTBENCH_API + "/leaderboard");
  if (seq !== state.seq) {
    return;
  }
  const board = data || {};
  const corrected = Number(board.corrected_items) || 0;
  $("tb-lb-corrected").textContent = corrected + (corrected === 1 ? " corrected item" : " corrected items") + " in the testbench.";
  const entries = board.items || [];
  [lbAudioTable, lbDocumentTable].forEach((table) => table.setEntries(entries.filter((entry) => entry.kind === table.kind)));
}

// ── Wiring ─────────────────────────────────────────────────────────────────

function wireTestbench() {
  $("tb-refresh").addEventListener("click", () => loadSection());
  $("tb-enable").addEventListener("click", (event) => setTestbenchEnabled(event.currentTarget, true));
  $("tb-disable").addEventListener("click", (event) => setTestbenchEnabled(event.currentTarget, false));
  ["tb-filter-kind", "tb-filter-status"].forEach((id) => {
    $(id).addEventListener("cds-select-selected", (event) => {
      if (event.target === event.currentTarget) {
        reloadItems();
      }
    });
  });
  $("tb-file-button").addEventListener("cds-file-uploader-button-changed", (event) => {
    const files = (event.detail && event.detail.addedFiles) || [];
    if (files.length) {
      setContributeFile(files[0]);
    } else {
      showError(new ApiError(0, "unsupported_file", "That file type is not accepted. Use audio, PDF, PNG, JPEG or TIFF."));
    }
  });
  $("tb-submit").addEventListener("click", (event) => contribute(event.currentTarget));
  $("tb-contribute-cancel").addEventListener("click", () => {
    $("contribute-modal").open = false;
  });
  $("tb-detail-close").addEventListener("click", () => {
    closeDetail();
    selectTestbenchTab("items");
  });
  $("tb-detail-delete").addEventListener("click", (event) => deleteItem(event.currentTarget, tb.openId));
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

function init() {
  buildNavigation();
  $("workers-table").appendChild(workersTable.root);
  $("credentials-table").appendChild(credentialsTable.root);
  mountJobsTable(false);
  $("baselines-table").appendChild(baselinesTable.root);
  $("releases-table").appendChild(releasesTable.root);
  $("items-table").appendChild(itemsTable.root);
  $("runs-table").appendChild(runsTable.root);
  $("lb-audio-table").appendChild(lbAudioTable.root);
  $("lb-document-table").appendChild(lbDocumentTable.root);

  // The Sign in button and Enter in the token field submit; a form-associated button
  // may also fire `submit`, which must not submit a second time.
  $("login-form").addEventListener("submit", (event) => event.preventDefault());
  $("login-submit").addEventListener("click", submitLogin);
  $("login-token").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submitLogin();
    }
  });

  $("sign-out").addEventListener("click", () => signOut(""));
  $("account-sign-out").addEventListener("click", () => signOut(""));
  $("new-org").addEventListener("click", () => {
    closePanels();
    const input = $("new-org-id");
    input.value = "";
    input.removeAttribute("invalid");
    $("new-org-modal").open = true;
  });
  $("new-org-submit").addEventListener("click", (event) => createOrganization(event.currentTarget));
  $("new-org-cancel").addEventListener("click", () => {
    $("new-org-modal").open = false;
  });
  $("new-org-id").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      createOrganization($("new-org-submit"));
    }
  });
  $("settings-refresh").addEventListener("click", () => loadSection());
  $("banner").addEventListener("cds-notification-closed", () => hideBanner());

  $("confirm-ok").addEventListener("click", () => closeConfirmation(true));
  $("confirm-cancel").addEventListener("click", () => closeConfirmation(false));
  $("confirm").addEventListener("cds-modal-closed", (event) => {
    if (event.target === $("confirm") && settleConfirmation) {
      settleConfirmation(false);
    }
  });
  // The issued token closes only through Done, which also clears it.
  $("issued").addEventListener("cds-modal-beingclosed", (event) => {
    if (event.target === $("issued")) {
      event.preventDefault();
    }
  });
  $("issued-done").addEventListener("click", clearIssued);
  wireTestbench();

  window.addEventListener("hashchange", () => {
    if (state.token && state.org) {
      route();
    }
  });

  state.token = loadToken();
  if (state.token) {
    enter();
  } else {
    showLogin("");
  }
}

init();
