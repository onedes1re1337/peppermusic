let HAS_TG = false;
let INIT_DATA = "dev";

try {
  const w = window.Telegram?.WebApp;
  if (w?.initData) {
    HAS_TG = true;
    INIT_DATA = w.initData;
    w.ready();
    w.expand();
    w.setHeaderColor?.("#06101d");
    w.setBackgroundColor?.("#06101d");
  }
} catch (_) {}

const $ = (s) => document.getElementById(s);

const $q = $("q");
const $qClear = $("q-clear");
const $list = $("list");
const $player = $("player");
const $searchBox = $("search-box");
const $tabSearch = $("tab-search");
const $tabFav = $("tab-favorites");

const $pArt = $("p-art");
const $pTitle = $("p-title");
const $pArtist = $("p-artist");
const $pBar = $("p-bar");
const $pCur = $("p-cur");
const $pTotal = $("p-total");

const $btnPP = $("btn-pp");
const $btnPrev = $("btn-prev");
const $btnNext = $("btn-next");
const $btnDL = $("btn-dl");
const $btnFav = $("btn-fav");
const $volume = $("volume");
const $progressHit = $("progress-hit");

const iconPlay = $btnPP.querySelector(".icon-play");
const iconPause = $btnPP.querySelector(".icon-pause");

const audio = new Audio();
audio.preload = "auto";

const STORAGE_KEYS = { volume: "peppermusic:volume" };

const FALLBACK_ART =
  "data:image/svg+xml;charset=UTF-8," +
  encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#7c3aed"/><stop offset="100%" stop-color="#06b6d4"/></linearGradient></defs>
      <rect width="96" height="96" rx="18" fill="#0d1628"/>
      <circle cx="56" cy="34" r="14" fill="url(#g)" opacity=".95"/>
      <rect x="28" y="22" width="10" height="44" rx="5" fill="url(#g)"/>
      <path d="M38 26c18 0 30-5 30-12v41" fill="none" stroke="url(#g)" stroke-width="8" stroke-linecap="round"/>
    </svg>
  `);

let cur = null;
let isPlaying = false;
let currentView = "search";
let searchTimer = null;
let lastTracks = [];
let favoriteTracks = [];
let currentQueue = [];
let currentIndex = -1;

let prevTapTimer = null;
const PREV_DOUBLE_TAP_MS = 280;

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
}

function fmtTime(sec) {
  if (!sec || !isFinite(sec)) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function haptic(style) {
  if (!HAS_TG) return;
  try { Telegram.WebApp.HapticFeedback.impactOccurred(style); } catch (_) {}
}

async function api(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: {
      "X-Init-Data": INIT_DATA,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!r.ok) throw new Error(await r.text().catch(() => String(r.status)));
  const ct = r.headers.get("content-type") || "";
  return ct.includes("application/json") ? r.json() : r.text();
}

function artUrl(track) {
  return track?.artwork_url || (track?.id ? `/api/artwork/${track.id}` : FALLBACK_ART);
}

function setImgSrc(img, src) {
  const url = src || FALLBACK_ART;
  img.src = url;
  img.classList.toggle("fallback", url === FALLBACK_ART);
  img.onerror = () => {
    img.onerror = null;
    img.src = FALLBACK_ART;
    img.classList.add("fallback");
  };
}

function eqHTML(paused) {
  return `<div class="eq${paused ? " paused" : ""}"><div class="eq-bar"></div><div class="eq-bar"></div><div class="eq-bar"></div><div class="eq-bar"></div></div>`;
}

function showStatus(emoji, text) {
  $list.innerHTML = `<div class="status"><span class="status-emoji">${emoji}</span>${text}</div>`;
}

function showShimmer(count = 6) {
  let html = '<div class="shimmer">';
  for (let i = 0; i < count; i++) {
    html += `<div class="shimmer-row"><div class="sh-circle"></div><div class="sh-lines"><div class="sh-line"></div><div class="sh-line"></div></div></div>`;
  }
  html += "</div>";
  $list.innerHTML = html;
}

function isFav(trackId) {
  return favoriteTracks.some((t) => String(t.id) === String(trackId));
}

async function loadFavorites() {
  try {
    const data = await api("GET", "/api/favorites");
    favoriteTracks = data.tracks || [];
    syncFavoriteButtons();
    if (currentView === "favorites") renderFavorites();
  } catch (e) {
    console.error("favorites load", e);
  }
}

async function toggleFavorite(track) {
  if (!track?.id) return;

  const fav = isFav(track.id);
  try {
    await api(fav ? "DELETE" : "POST", `/api/favorites/${track.id}`);
    await loadFavorites();
    haptic("light");
  } catch (e) {
    console.error("favorite toggle", e);
  }
}

function syncFavoriteButtons() {
  document.querySelectorAll(".t-fav").forEach((btn) => {
    btn.classList.toggle("active", isFav(btn.dataset.id));
  });
  $btnFav.classList.toggle("active", !!cur && isFav(cur.id));
}

function setQueue(tracks, idx) {
  currentQueue = tracks || [];
  currentIndex = idx;
}

function switchTab(view) {
  currentView = view;
  $tabSearch.classList.toggle("active", view === "search");
  $tabFav.classList.toggle("active", view === "favorites");
  $searchBox.classList.toggle("hidden", view !== "search");

  if (view === "favorites") {
    renderFavorites();
  } else if (lastTracks.length) {
    renderTracks(lastTracks, "search");
  } else {
    $list.innerHTML = "";
  }
}

function renderFavorites() {
  if (!favoriteTracks.length) {
    showStatus("💔", "Пока нет избранных треков.<br><small>Жми на сердечко рядом с треком — и он сохранится на сервере.</small>");
    return;
  }
  renderTracks(favoriteTracks, "favorites");
}

function renderTracks(tracks, source = currentView) {
  $list._tracks = tracks;
  $list._source = source;

  $list.innerHTML = tracks.map((t, i) => {
    const active = cur && String(cur.id) === String(t.id);
    return `
      <article class="track${active ? " playing" : ""}" data-id="${esc(String(t.id))}" data-idx="${i}">
        <div class="t-art-wrap">
          <img class="t-art" src="${esc(artUrl(t))}" alt="cover" loading="lazy"/>
          <div class="t-play-btn">${active && isPlaying ? eqHTML(false) : '<svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg>'}</div>
        </div>
        <div class="t-meta">
          <div class="t-name">${esc(t.title)}</div>
          <div class="t-artist">${esc(t.artist)}</div>
          <div class="t-dur">${esc(t.duration || "0:00")}</div>
        </div>
        <div class="t-side">
          <button class="t-fav${isFav(t.id) ? " active" : ""}" data-id="${esc(String(t.id))}" title="В избранное" aria-label="В избранное">
            <svg viewBox="0 0 24 24"><path d="M12 21s-6.716-4.35-9.193-8.091C.812 9.824 2.002 5.5 5.9 4.427 8.161 3.805 10.112 4.75 12 6.7c1.888-1.95 3.839-2.895 6.1-2.273 3.898 1.073 5.088 5.397 3.093 8.482C18.716 16.65 12 21 12 21z"/></svg>
          </button>
          <button class="t-action" data-id="${esc(String(t.id))}" aria-label="Play track">
            ${active && isPlaying ? eqHTML(false) : '<svg viewBox="0 0 24 24"><polygon points="6,3 20,12 6,21"/></svg>'}
          </button>
        </div>
      </article>`;
  }).join("");

  $list.querySelectorAll(".track").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest(".t-fav") || e.target.closest(".t-action")) return;
      onTrackClick(el);
    });
  });

  $list.querySelectorAll(".t-action").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      onTrackClick(btn.closest(".track"));
    });
  });

  $list.querySelectorAll(".t-fav").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const row = btn.closest(".track");
      const idx = Number(row.dataset.idx);
      await toggleFavorite($list._tracks[idx]);
      if (currentView === "favorites") renderFavorites();
      else renderTracks(lastTracks, "search");
    });
  });

  $list.querySelectorAll(".t-art").forEach((img) => {
    img.addEventListener("error", () => {
      img.src = FALLBACK_ART;
      img.classList.add("fallback");
    }, { once: true });
  });

  syncFavoriteButtons();
}

function onTrackClick(el) {
  const idx = Number(el.dataset.idx);
  const track = $list._tracks[idx];
  if (!track) return;

  if (cur && String(cur.id) === String(track.id)) {
    togglePlay();
    return;
  }

  setQueue($list._tracks, idx);
  playTrack(track);
}

function playTrack(track) {
  cur = { ...track, artwork_url: artUrl(track) };
  $pTitle.textContent = track.title || "Без названия";
  $pArtist.textContent = track.artist || "SoundCloud";
  $pCur.textContent = "0:00";
  $pTotal.textContent = track.duration || "0:00";
  $pBar.style.width = "0%";
  setImgSrc($pArt, artUrl(track));
  $player.classList.remove("hidden");

  audio.src = `/api/stream/${track.id}?init_data=${encodeURIComponent(INIT_DATA)}`;
  audio.load();
  audio.play().catch(console.error);
  setPlaying(true);
  syncTrackList();
  syncFavoriteButtons();
}

function setPlaying(v) {
  isPlaying = v;
  iconPlay.classList.toggle("hidden", v);
  iconPause.classList.toggle("hidden", !v);
}

function togglePlay() {
  if (!cur) return;
  if (audio.paused) audio.play().catch(console.error);
  else audio.pause();
}

function playAt(index) {
  if (!currentQueue.length) return;
  if (index < 0) index = currentQueue.length - 1;
  if (index >= currentQueue.length) index = 0;
  currentIndex = index;
  playTrack(currentQueue[currentIndex]);
}

function syncTrackList() {
  document.querySelectorAll(".track").forEach((el) => {
    const active = cur && String(cur.id) === String(el.dataset.id);
    el.classList.toggle("playing", active);
    const action = el.querySelector(".t-action");
    const overlay = el.querySelector(".t-play-btn");
    if (!action || !overlay) return;
    if (active && isPlaying) {
      action.innerHTML = eqHTML(false);
      overlay.innerHTML = eqHTML(false);
    } else if (active && !isPlaying) {
      action.innerHTML = eqHTML(true);
      overlay.innerHTML = eqHTML(true);
    } else {
      action.innerHTML = '<svg viewBox="0 0 24 24"><polygon points="6,3 20,12 6,21"/></svg>';
      overlay.innerHTML = '<svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg>';
    }
  });
  syncFavoriteButtons();
}

function initVolume() {
  let vol = 1;
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.volume);
    if (raw !== null) vol = Number(raw);
  } catch (_) {}
  if (!Number.isFinite(vol)) vol = 1;
  vol = Math.max(0, Math.min(1, vol));
  audio.volume = vol;
  $volume.value = String(vol);
  $volume.addEventListener("input", () => {
    const v = Math.max(0, Math.min(1, Number($volume.value)));
    audio.volume = v;
    localStorage.setItem(STORAGE_KEYS.volume, String(v));
  });
}

async function doSearch(query) {
  showShimmer();
  try {
    const data = await api("GET", `/api/search?q=${encodeURIComponent(query)}`);
    lastTracks = data.tracks || [];
    if (!lastTracks.length) {
      showStatus("😔", "Ничего не найдено");
      return;
    }
    renderTracks(lastTracks, "search");
  } catch (e) {
    showStatus("❌", `Ошибка поиска<br><small>${esc(e.message)}</small>`);
  }
}

$q.addEventListener("input", () => {
  const value = $q.value.trim();
  $qClear.classList.toggle("hidden", !$q.value);
  clearTimeout(searchTimer);

  if (value.length < 2) {
    if (currentView === "search") $list.innerHTML = "";
    lastTracks = [];
    return;
  }

  if (currentView !== "search") switchTab("search");
  searchTimer = setTimeout(() => doSearch(value), 320);
});

$qClear.addEventListener("click", () => {
  $q.value = "";
  $qClear.classList.add("hidden");
  lastTracks = [];
  if (currentView === "search") $list.innerHTML = "";
  $q.focus();
});

$tabSearch.addEventListener("click", () => switchTab("search"));
$tabFav.addEventListener("click", () => switchTab("favorites"));
$btnPP.addEventListener("click", togglePlay);
$btnPrev.addEventListener("click", () => {
  if (!cur) return;

  if (prevTapTimer) {
    clearTimeout(prevTapTimer);
    prevTapTimer = null;
    playAt(currentIndex - 1);
    return;
  }

  prevTapTimer = setTimeout(() => {
    prevTapTimer = null;
    audio.currentTime = 0;

    if (audio.paused) {
      audio.play().catch(console.error);
    }
  }, PREV_DOUBLE_TAP_MS);
});
$btnNext.addEventListener("click", () => playAt(currentIndex + 1));
$btnFav.addEventListener("click", async () => { if (cur) await toggleFavorite(cur); });

$progressHit.addEventListener("click", (e) => {
  if (!audio.duration) return;
  const bar = e.currentTarget.querySelector(".mini-player-progress-track");
  const rect = bar.getBoundingClientRect();
  const pct = (e.clientX - rect.left) / rect.width;
  audio.currentTime = Math.max(0, Math.min(audio.duration, pct * audio.duration));
});

$btnDL.addEventListener("click", async () => {
  if (!cur) return;
  $btnDL.classList.add("loading");
  $btnDL.classList.remove("ok", "err");
  try {
    await api("POST", `/api/send/${cur.id}`);
    $btnDL.classList.add("ok");
    if (HAS_TG) {
      Telegram.WebApp.showAlert("Трек отправлен в чат 🎶");
    }
  } catch (e) {
    console.error(e);
    $btnDL.classList.add("err");
  } finally {
    $btnDL.classList.remove("loading");
    setTimeout(() => $btnDL.classList.remove("ok", "err"), 2400);
  }
});

audio.addEventListener("timeupdate", () => {
  if (!audio.duration) return;
  $pBar.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
  $pCur.textContent = fmtTime(audio.currentTime);
});
audio.addEventListener("loadedmetadata", () => { $pTotal.textContent = fmtTime(audio.duration); });
audio.addEventListener("play", () => { setPlaying(true); syncTrackList(); });
audio.addEventListener("pause", () => { setPlaying(false); syncTrackList(); });
audio.addEventListener("ended", () => playAt(currentIndex + 1));

initVolume();
loadFavorites();
if (!HAS_TG) setTimeout(() => $q.focus(), 250);