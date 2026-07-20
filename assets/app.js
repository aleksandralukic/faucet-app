const STATUS_ORDER = ["down", "degraded", "manual", "up", "unknown"];
const STATUS_LABEL = {
  up: "Up",
  degraded: "Degraded",
  down: "Down",
  manual: "Manual",
  unknown: "Unknown",
};

const state = {
  faucets: [],
  status: {},
  generatedAt: null,
  query: "",
  active: new Set(),
  hideCaptcha: false,
  hideWallet: false,
};

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function relTime(iso) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const hours = Math.round(diff / 3.6e6);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

async function load() {
  try {
    const [faucets, status] = await Promise.all([
      fetch("data/faucets.json").then((r) => r.json()),
      // status.json only exists after the first check run.
      fetch("data/status.json").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);

    state.faucets = faucets;
    if (status) {
      state.generatedAt = status.generatedAt;
      for (const r of status.results) state.status[r.id] = r;
    }
  } catch (e) {
    $("list").innerHTML = `<p class="error">Could not load faucet data: ${esc(e.message)}</p>`;
    return;
  }

  renderSummary();
  renderFilters();
  wireControls();
  render();
}

function statusOf(f) {
  return state.status[f.id]?.status ?? "unknown";
}

function renderSummary() {
  const counts = {};
  for (const f of state.faucets) {
    const s = statusOf(f);
    counts[s] = (counts[s] || 0) + 1;
  }

  $("summary").innerHTML = STATUS_ORDER
    .filter((s) => counts[s])
    .map((s) => `<span class="pill"><span class="dot ${s}"></span>${counts[s]} ${esc(STATUS_LABEL[s])}</span>`)
    .join("");

  $("generated").textContent = state.generatedAt
    ? `Last checked ${new Date(state.generatedAt).toLocaleString()} (${relTime(state.generatedAt)})`
    : "No status data yet — run scripts/check_faucets.py or wait for the daily job.";
}

function renderFilters() {
  const present = STATUS_ORDER.filter((s) => state.faucets.some((f) => statusOf(f) === s));
  $("statusFilters").innerHTML = present
    .map((s) => `<button class="filter" data-status="${s}" aria-pressed="false"><span class="dot ${s}"></span> ${esc(STATUS_LABEL[s])}</button>`)
    .join("");

  $("statusFilters").addEventListener("click", (e) => {
    const btn = e.target.closest(".filter");
    if (!btn) return;
    const s = btn.dataset.status;
    if (state.active.has(s)) state.active.delete(s);
    else state.active.add(s);
    btn.setAttribute("aria-pressed", String(state.active.has(s)));
    render();
  });
}

function wireControls() {
  $("search").addEventListener("input", (e) => {
    state.query = e.target.value.trim().toLowerCase();
    render();
  });
  $("hideCaptcha").addEventListener("change", (e) => {
    state.hideCaptcha = e.target.checked;
    render();
  });
  $("hideWallet").addEventListener("change", (e) => {
    state.hideWallet = e.target.checked;
    render();
  });
}

function matches(f) {
  const s = statusOf(f);
  if (state.active.size && !state.active.has(s)) return false;
  if (state.hideCaptcha && f.requiresCaptcha) return false;
  if (state.hideWallet && f.requiresWallet) return false;
  if (!state.query) return true;

  const haystack = [f.currency, f.name, f.network, f.notes].join(" ").toLowerCase();
  return haystack.includes(state.query);
}

function sparkline(history) {
  if (!history?.length) return "";
  const bars = history.slice(-14)
    .map((h) => `<i class="${esc(h)}" title="${esc(STATUS_LABEL[h] || h)}"></i>`)
    .join("");
  return `<span class="spark" title="Last ${Math.min(history.length, 14)} checks">${bars}</span>`;
}

function card(f) {
  const st = state.status[f.id];
  const s = statusOf(f);

  const tags = [];
  if (f.requiresCaptcha) tags.push("captcha");
  if (f.requiresWallet) tags.push("wallet connect");
  if (st?.responseMs != null) tags.push(`${st.responseMs} ms`);
  if (st?.httpStatus != null) tags.push(`HTTP ${st.httpStatus}`);

  return `
    <article class="card ${s}">
      <div class="card-top">
        <span class="ticker">${esc(f.currency)}</span>
        <a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.name)}</a>
        <span class="network">${esc(f.network)}</span>
        <span class="status-line">
          <span class="dot ${s}"></span>${esc(STATUS_LABEL[s] || s)}${sparkline(st?.history)}
        </span>
      </div>
      ${f.notes ? `<p class="notes">${esc(f.notes)}</p>` : ""}
      <div class="meta">
        ${tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
      </div>
      ${st?.reason ? `<p class="reason">${esc(st.reason)}</p>` : ""}
      ${st?.redirectedTo ? `<p class="reason">Redirects to ${esc(st.redirectedTo)} — consider updating the URL.</p>` : ""}
    </article>`;
}

function render() {
  const visible = state.faucets
    .filter(matches)
    .sort((a, b) => {
      const d = STATUS_ORDER.indexOf(statusOf(a)) - STATUS_ORDER.indexOf(statusOf(b));
      return d !== 0 ? d : a.currency.localeCompare(b.currency);
    });

  $("list").innerHTML = visible.map(card).join("");
  $("empty").hidden = visible.length > 0;
  $("count").textContent = `${visible.length} of ${state.faucets.length} faucets`;
}

load();
