/* Local Events — vanilla client. Missing source values render as "not listed". */

const $ = (sel) => document.querySelector(sel);
const NOT_LISTED = "not listed";

const state = {
  location: "",
  radius: 40,
  window: "month",
  sort: "date",
  genres: new Set(),
  facets: {},
  events: [],
  loading: false,
};

const form = $("#search-form");
const statusEl = $("#status");
const resultsEl = $("#results");
const facetsEl = $("#facets");
const candidatesEl = $("#candidates");

async function search({ keepFacets = false } = {}) {
  if (!state.location.trim()) return;

  hideCandidates();
  state.loading = true;
  setStatus(`Searching near ${state.location}…`);
  resultsEl.setAttribute("aria-busy", "true");
  form.querySelector(".primary").disabled = true;

  const params = new URLSearchParams({
    location: state.location,
    radius_km: String(state.radius),
    window: state.window,
    sort: state.sort,
    limit: "40",
  });
  state.genres.forEach((g) => params.append("genres", g));

  try {
    const res = await fetch(`/api/events?${params}`);
    const data = await res.json();

    if (!res.ok) {
      const err = data?.error || {};
      if (res.status === 409 && err.code === "ambiguous_location" && err.candidates?.length) {
        showCandidates(err.candidates, err.message);
        state.events = [];
        render();
        return;
      }
      hideCandidates();
      const msg = err.message || data?.detail || "Something went wrong.";
      state.events = [];
      render();
      setStatus(msg, true);
      return;
    }

    hideCandidates();

    state.events = data.events;
    if (!keepFacets) state.facets = data.genre_facets;

    renderFacets();
    render();
    setStatus(summarize(data));
  } catch (err) {
    state.events = [];
    render();
    setStatus("Could not reach the server. Is it running?", true);
  } finally {
    state.loading = false;
    resultsEl.removeAttribute("aria-busy");
    form.querySelector(".primary").disabled = false;
  }
}

function summarize(data) {
  const n = data.events.length;
  const where = data.location.label;
  const failed = data.providers.filter((p) => !p.ok);
  let text = n === 0
    ? `No events found near ${where}. Try a wider radius or a longer date range.`
    : `${n} event${n === 1 ? "" : "s"} near ${where}`;
  if (failed.length) {
    text += ` — ${failed.map((p) => `${p.name} unavailable`).join(", ")}`;
  }
  return text;
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

function showCandidates(candidates, message) {
  candidatesEl.hidden = false;
  candidatesEl.innerHTML = "";
  candidates.forEach((c) => {
    const b = document.createElement("button");
    b.type = "button";
    const extra = c.upcoming_event_count != null ? ` (${c.upcoming_event_count})` : "";
    b.textContent = `${c.label}${extra}`;
    b.onclick = () => {
      $("#location").value = c.label;
      hideCandidates();
      form.requestSubmit();
    };
    candidatesEl.appendChild(b);
  });
  setStatus(message || "Pick a place.");
}

function hideCandidates() {
  candidatesEl.hidden = true;
  candidatesEl.innerHTML = "";
}

const MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
const DAYS = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const WARN = new Set(["Cancelled", "Postponed"]);

function listed(value) {
  if (value == null || value === "") return NOT_LISTED;
  return String(value);
}

function listedClass(value) {
  return value == null || value === "" ? " missing" : "";
}

function render() {
  resultsEl.innerHTML = "";
  if (!state.events.length) {
    if (!state.loading && state.location && candidatesEl.hidden) {
      resultsEl.innerHTML = `<p class="empty">Nothing here yet.<br />Widen the radius or pick a longer date range.</p>`;
    }
    return;
  }
  const frag = document.createDocumentFragment();
  state.events.forEach((e) => frag.appendChild(card(e)));
  resultsEl.appendChild(frag);
}

function card(e) {
  const el = document.createElement("article");
  el.className = "card";

  const [y, m, d] = e.start_date.split("-").map(Number);
  const dt = new Date(y, m - 1, d);

  const namedPerformers = e.performers.filter((p) => p.name);
  const headliner = namedPerformers.find((p) => p.is_headliner) || namedPerformers[0];
  const support = namedPerformers.filter((p) => p !== headliner).slice(0, 3);
  const primary = e.offers.find((o) => o.is_primary) || e.offers[0];

  const cityRegion = e.venue.city && e.venue.region
    ? `${e.venue.city}, ${e.venue.region}`
    : (e.venue.city || e.venue.region || null);

  const showtime = formatTime(e.start_time);
  const door = formatTime(e.door_time);

  const titleHtml = e.title
    ? (e.url
      ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>`
      : esc(e.title))
    : NOT_LISTED;

  el.innerHTML = `
    <div class="date">
      <div class="mon">${MONTHS[dt.getMonth()]}</div>
      <div class="day">${dt.getDate()}</div>
      <div class="dow">${DAYS[dt.getDay()]}</div>
    </div>
    <div class="body">
      <h2 class="title${e.title ? "" : " missing"}">${titleHtml}</h2>
      <div class="meta">
        <span class="${listedClass(e.venue.name)}">${esc(listed(e.venue.name))}</span>
        ·
        <span class="${listedClass(cityRegion)}">${esc(listed(cityRegion))}</span>
      </div>
      <div class="meta times">
        Show <span class="${listedClass(showtime)}">${esc(listed(showtime))}</span>
        · Door <span class="${listedClass(door)}">${esc(listed(door))}</span>
        · Capacity <span class="${listedClass(e.venue.capacity)}">${esc(listed(e.venue.capacity))}</span>
      </div>
      ${support.length ? `<div class="lineup">with <strong>${support.map((p) => esc(p.name)).join("</strong>, <strong>")}</strong>${
        namedPerformers.length - support.length - 1 > 0 ? ` +${namedPerformers.length - support.length - 1} more` : ""}</div>` : ""}
      <div class="tags">${e.signals.tags.map(tag).join("")}</div>
    </div>
    <div class="actions">
      ${e.signals.distance_km != null ? `<span class="dist">${e.signals.distance_km} km away</span>` : `<span class="dist missing">${NOT_LISTED}</span>`}
      ${primary ? `<a class="tickets" href="${esc(primary.url)}" target="_blank" rel="noopener">${esc(listed(primary.seller))} →</a>` : ""}
    </div>`;
  return el;
}

function formatTime(t) {
  if (!t) return null;
  // API serializes time as "20:00:00"
  const parts = String(t).split(":");
  const h = Number(parts[0]);
  const min = parts[1] || "00";
  if (Number.isNaN(h)) return t;
  const suffix = h >= 12 ? "pm" : "am";
  const hour = ((h + 11) % 12) + 1;
  return `${hour}:${min} ${suffix}`;
}

function tag(t) {
  const cls = WARN.has(t) ? "tag warn" : "tag";
  return `<span class="${cls}">${esc(t)}</span>`;
}

function renderFacets() {
  const entries = Object.entries(state.facets);
  facetsEl.hidden = entries.length === 0;
  facetsEl.innerHTML = "";
  entries.forEach(([genre, count]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${genre.replace(/-/g, " ")} (${count})`;
    b.className = state.genres.has(genre) ? "active" : "";
    b.onclick = () => {
      state.genres.has(genre) ? state.genres.delete(genre) : state.genres.add(genre);
      search({ keepFacets: true });
    };
    facetsEl.appendChild(b);
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  state.location = $("#location").value;
  state.radius = Number($("#radius").value);
  state.sort = $("#sort").value;
  state.genres.clear();
  search();
});

$("#sort").addEventListener("change", () => {
  state.sort = $("#sort").value;
  if (state.events.length) search({ keepFacets: true });
});

$("#windows").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-window]");
  if (!btn) return;
  [...ev.currentTarget.children].forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  state.window = btn.dataset.window;
  if (state.location) search({ keepFacets: true });
});

$("#geo-btn").addEventListener("click", () => {
  if (!navigator.geolocation) return setStatus("Geolocation is not available.", true);
  setStatus("Getting your location…");
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      $("#location").value = `${coords.latitude.toFixed(4)},${coords.longitude.toFixed(4)}`;
      form.requestSubmit();
    },
    () => setStatus("Could not get your location — type a city instead.", true)
  );
});
