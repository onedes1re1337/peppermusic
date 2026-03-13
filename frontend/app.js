
  /* ═══════════════════════════════════════
     PepperMusic — App Logic
     ═══════════════════════════════════════ */

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
  const $tabPlaylists = $("tab-playlists");
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
  const $btnShuffle = $("btn-shuffle");
  const $btnRepeat = $("btn-repeat");
  const $btnDL = $("btn-dl");
  const $btnFav = $("btn-fav");
  const $progressHit = $("progress-hit");

  const $modalCreatePl = $("modal-create-pl");
  const $newPlName = $("new-pl-name");
  const $newPlSubmit = $("new-pl-submit");
  const $modalAddToPl = $("modal-add-to-pl");
  const $modalPlList = $("modal-pl-list");
  const $toastContainer = $("toast-container");

  const $modalConfirm = $("modal-confirm");
  const $confirmIcon = $("confirm-icon");
  const $confirmTitle = $("confirm-title");
  const $confirmText = $("confirm-text");
  const $confirmCancel = $("confirm-cancel");
  const $confirmOk = $("confirm-ok");
  let _confirmResolve = null;

  const $modalRenamePl  = $("modal-rename-pl");
const $renamePlName   = $("rename-pl-name");
const $renamePlSubmit = $("rename-pl-submit");
let _renamePlId = null;

  const $modalImportYM = $("modal-import-ym");
  const $importYMUrl = $("import-ym-url");
  const $importYMSubmit = $("import-ym-submit");
  const $importYMStatus = $("import-ym-status");

  const iconPlay = $btnPP.querySelector(".icon-play");
  const iconPause = $btnPP.querySelector(".icon-pause");

  const audio = new Audio();
  audio.preload = "auto";

  const STORAGE_KEYS = { volume: "peppermusic:volume" };

  const FALLBACK_ART =
    "data:image/svg+xml;charset=UTF-8," +
    encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#7c3aed"/><stop offset="100%" stop-color="#06b6d4"/></linearGradient></defs><rect width="96" height="96" rx="18" fill="#0d1628"/><circle cx="56" cy="34" r="14" fill="url(#g)" opacity=".95"/><rect x="28" y="22" width="10" height="44" rx="5" fill="url(#g)"/><path d="M38 26c18 0 30-5 30-12v41" fill="none" stroke="url(#g)" stroke-width="8" stroke-linecap="round"/></svg>`);

  // ── State ──
  let cur = null;
  let isPlaying = false;
  let currentView = "search";
  let searchTimer = null;
  let lastTracks = [];
  let favoriteTracks = [];
  let currentQueue = [];
  let currentIndex = -1;
  let activeSource = "all";
  let shuffleMode = false;
  let repeatMode = "none"; // none | all | one
  let playlists = [];
  let currentPlaylistDetail = null;
  let pendingTrackForPlaylist = null;
  let prevTapTimer = null;
  const PREV_DOUBLE_TAP_MS = 280;

  // ── Helpers ──
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

  function toast(emoji, text) {
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = `<span>${emoji}</span> ${esc(text)}`;
    $toastContainer.appendChild(el);
    setTimeout(() => el.remove(), 2800);
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
    img.onerror = () => { img.onerror = null; img.src = FALLBACK_ART; img.classList.add("fallback"); };
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

  function sourceBadgeHTML(source) {
    if (source === "deezer") {
      return `<span class="t-source-badge badge-spotify">DZ</span>`;
    }
    if (source === "youtube") {
      return `<span class="t-source-badge badge-youtube">YT</span>`;
    }
    return `<span class="t-source-badge badge-soundcloud">SC</span>`;
  }

  function sourceTagHTML(source) {
    if (source === "deezer") {
      return `<span class="t-source-tag tag-spotify">Deezer</span>`;
    }
    if (source === "youtube") {
      return `<span class="t-source-tag tag-youtube">YouTube Music</span>`;
    }
    return `<span class="t-source-tag tag-soundcloud">SoundCloud</span>`;
  }

  function hideKeyboard() {
    const el = document.activeElement;
    if (!el) return;
    const tag = (el.tagName || "").toLowerCase();
    const isEditable =
      tag === "input" ||
      tag === "textarea" ||
      el.isContentEditable;

    if (isEditable) {
      el.blur();
    }
  }

  $q.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      hideKeyboard();
    }
  });

  $newPlName.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      hideKeyboard();
    }
  });

  // ── Favorites ──
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
      toast(fav ? "💔" : "❤️", fav ? "Удалено из избранного" : "Добавлено в избранное");
    } catch (e) {
      console.error("favorite toggle", e);
      toast("❌", "Ошибка");
    }
  }

  function syncFavoriteButtons() {
    document.querySelectorAll(".t-fav").forEach((btn) => {
      btn.classList.toggle("active", isFav(btn.dataset.id));
    });
    $btnFav.classList.toggle("active", !!cur && isFav(cur.id));
  }

  // ── Queue / Shuffle ──
  function setQueue(tracks, idx) {
    currentQueue = [...tracks];
    currentIndex = idx;
  }

  function shuffleArray(arr) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // ── Tab switching ──
  function switchTab(view) {
    currentView = view;
    currentPlaylistDetail = null;
    $tabSearch.classList.toggle("active", view === "search");
    $tabPlaylists.classList.toggle("active", view === "playlists");
    $tabFav.classList.toggle("active", view === "favorites");
    
    // Скрыть основной поиск когда ушли с вкладки "Поиск"
    $searchBox.classList.toggle("hidden", view !== "search");

    if (view === "favorites") {
      renderFavorites();
    } else if (view === "playlists") {
      renderPlaylists();
    } else if (lastTracks.length) {
      renderTracks(lastTracks, "search");
    } else {
      $list.innerHTML = "";
    }
  }

  function renderFavorites() {
    if (!favoriteTracks.length) {
      showStatus("💔", "Пока нет избранных треков.<br><small>Жми на сердечко — и трек сохранится.</small>");
      return;
    }

    $list._tracks = favoriteTracks;
    $list._source = "favorites";

    // Захватываем в замыкание — защита от перезаписи $list._tracks
    const boundTracks = favoriteTracks;

    const searchInputHtml = `
      <div class="status" style="padding:12px;">
        <input type="text" id="fav-search-input" placeholder="Название трека или артист">
      </div>`;

    $list.innerHTML = searchInputHtml + `
      <div class="track-list" id="fav-tracks-list">
        ${favoriteTracks.map((t, i) => {
          const active = cur && String(cur.id) === String(t.id);
          return `
          <article class="track${active ? " playing" : ""}" data-id="${esc(String(t.id))}" data-idx="${i}">
            <div class="t-art-wrap">
              <img class="t-art" src="${esc(artUrl(t))}" alt="" loading="lazy"/>
              <div class="t-play-btn">${active && isPlaying ? eqHTML(false) : '<svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg>'}</div>
            </div>
            <div class="t-meta">
              <div class="t-name">${esc(t.title)}</div>
              <div class="t-artist">${esc(t.artist)}</div>
              <div class="t-info-row">
                <span class="t-dur">${esc(t.duration || "0:00")}</span>
                ${sourceTagHTML(t.source || "soundcloud")}
              </div>
            </div>
            <div class="t-side">
              <button class="t-fav active" data-id="${esc(String(t.id))}"><svg viewBox="0 0 24 24"><path d="M12 21s-6.716-4.35-9.193-8.091C.812 9.824 2.002 5.5 5.9 4.427 8.161 3.805 10.112 4.75 12 6.7c1.888-1.95 3.839-2.895 6.1-2.273 3.898 1.073 5.088 5.397 3.093 8.482C18.716 16.65 12 21 12 21z"/></svg></button>
              <button class="t-action">${active && isPlaying ? eqHTML(false) : '<svg viewBox="0 0 24 24"><polygon points="6,3 20,12 6,21"/></svg>'}</button>
            </div>
          </article>`;
        }).join("")}
      </div>`;

    const searchInput = $("fav-search-input");
    const listEl = $("fav-tracks-list");

    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") hideKeyboard();
    });

    searchInput.addEventListener("input", (e) => {
      const q = e.target.value.trim().toLowerCase();
      Array.from(listEl.querySelectorAll(".track")).forEach(row => {
        const title = row.querySelector(".t-name").textContent.toLowerCase();
        const artist = row.querySelector(".t-artist").textContent.toLowerCase();
        row.style.display = (title.includes(q) || artist.includes(q)) ? "" : "none";
      });
    });

    // Все обработчики используют boundTracks, а не $list._tracks
    listEl.querySelectorAll(".track").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest(".t-fav") || e.target.closest(".t-action")) return;
        onTrackClick(el, boundTracks);
      });
    });
    listEl.querySelectorAll(".t-action").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        onTrackClick(btn.closest(".track"), boundTracks);
      });
    });
    listEl.querySelectorAll(".t-art-wrap").forEach((wrap) => {
      wrap.addEventListener("click", (e) => {
        e.stopPropagation();
        onTrackClick(wrap.closest(".track"), boundTracks);
      });
    });
    listEl.querySelectorAll(".t-fav").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const idx = Number(btn.closest(".track").dataset.idx);
        await toggleFavorite(boundTracks[idx] || { id: btn.dataset.id });
        renderFavorites();
      });
    });
  }

  // ── Track rendering ──
  function renderTracks(tracks, source = currentView) {
    $list._tracks = tracks;
    $list._source = source;

    $list.innerHTML = tracks.map((t, i) => {
      const active = cur && String(cur.id) === String(t.id);
      const src = t.source || "soundcloud";
      return `
        <article class="track${active ? " playing" : ""}" data-id="${esc(String(t.id))}" data-idx="${i}">
          <div class="t-art-wrap">
            <img class="t-art" src="${esc(artUrl(t))}" alt="" loading="lazy"/>
            ${sourceBadgeHTML(src)}
            <div class="t-play-btn">${active && isPlaying ? eqHTML(false) : '<svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg>'}</div>
          </div>
          <div class="t-meta">
            <div class="t-name">${esc(t.title)}</div>
            <div class="t-artist">${esc(t.artist)}</div>
            <div class="t-info-row">
              <span class="t-dur">${esc(t.duration || "0:00")}</span>
              ${sourceTagHTML(src)}
            </div>
          </div>
          <div class="t-side">
            <button class="t-add-pl" data-id="${esc(String(t.id))}" title="В плейлист" aria-label="Добавить в плейлист">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            </button>
            <button class="t-fav${isFav(t.id) ? " active" : ""}" data-id="${esc(String(t.id))}" title="В избранное" aria-label="В избранное">
              <svg viewBox="0 0 24 24"><path d="M12 21s-6.716-4.35-9.193-8.091C.812 9.824 2.002 5.5 5.9 4.427 8.161 3.805 10.112 4.75 12 6.7c1.888-1.95 3.839-2.895 6.1-2.273 3.898 1.073 5.088 5.397 3.093 8.482C18.716 16.65 12 21 12 21z"/></svg>
            </button>
            <button class="t-action" data-id="${esc(String(t.id))}" aria-label="Play">
              ${active && isPlaying ? eqHTML(false) : '<svg viewBox="0 0 24 24"><polygon points="6,3 20,12 6,21"/></svg>'}
            </button>
          </div>
        </article>`;
    }).join("");

    bindTrackEvents();
  }

  function bindTrackEvents() {
    // Захватываем треки в замыкание — защита от перезаписи $list._tracks
    const boundTracks = $list._tracks;

    $list.querySelectorAll(".track").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest(".t-fav") || e.target.closest(".t-action") || e.target.closest(".t-add-pl") || e.target.closest(".t-remove-pl")) return;
        onTrackClick(el, boundTracks);
      });
    });

    $list.querySelectorAll(".t-action").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        onTrackClick(btn.closest(".track"), boundTracks);
      });
    });

    $list.querySelectorAll(".t-fav").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const row = btn.closest(".track");
        const idx = Number(row.dataset.idx);
        const track = boundTracks[idx];
        await toggleFavorite(track);
        if (currentView === "favorites") renderFavorites();
        else if (currentView === "playlists" && currentPlaylistDetail) renderPlaylistDetail(currentPlaylistDetail);
        else if (lastTracks.length) renderTracks(lastTracks, "search");
      });
    });

    $list.querySelectorAll(".t-add-pl").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const row = btn.closest(".track");
        const idx = Number(row.dataset.idx);
        pendingTrackForPlaylist = boundTracks[idx];
        openAddToPlaylistModal();
      });
    });

    $list.querySelectorAll(".t-art").forEach((img) => {
      img.addEventListener("error", () => { img.src = FALLBACK_ART; img.classList.add("fallback"); }, { once: true });
    });

    syncFavoriteButtons();
  }

  function onTrackClick(el, tracksOverride) {
    const tracks = tracksOverride || $list._tracks;
    const idx = Number(el.dataset.idx);
    const track = tracks[idx];
    if (!track) return;

    if (cur && String(cur.id) === String(track.id)) {
      togglePlay();
      return;
    }

    setQueue(tracks, idx);
    playTrack(track);
  }

  // ── Playback ──
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
    if (repeatMode === "one") {
      audio.currentTime = 0;
      audio.play().catch(console.error);
      return;
    }
    if (index < 0) index = currentQueue.length - 1;
    if (index >= currentQueue.length) {
      if (repeatMode === "all") index = 0;
      else { setPlaying(false); return; }
    }
    currentIndex = index;
    playTrack(currentQueue[currentIndex]);
  }

  function playNext() {
    if (shuffleMode && currentQueue.length > 1) {
      let nextIdx;
      do { nextIdx = Math.floor(Math.random() * currentQueue.length); } while (nextIdx === currentIndex);
      currentIndex = nextIdx;
      playTrack(currentQueue[currentIndex]);
    } else {
      playAt(currentIndex + 1);
    }
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

  // ── Search ──
  async function doSearch(query) {
    showShimmer();
    try {
      const sourceParam = activeSource !== "all" ? `&source=${activeSource}` : "";
      const data = await api("GET", `/api/search?q=${encodeURIComponent(query)}${sourceParam}`);
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

  // ── Playlists ──
  async function loadPlaylists() {
    try {
      const data = await api("GET", "/api/playlists");
      playlists = data.playlists || [];
      if (currentView === "playlists" && !currentPlaylistDetail) renderPlaylists();
    } catch (e) {
      console.error("playlists load", e);
    }
  }

  function renderPlaylists() {
    let html = '<div class="playlist-grid">';

    // Create new card
    html += `<div class="pl-card-new" id="pl-create-btn">
      <svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      <span>Новый плейлист</span>
    </div>`;

    html += `<div class="pl-card-new" id="pl-import-ym-btn">
      <svg viewBox="0 0 24 24"><path d="M12 3v10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="m8 9 4 4 4-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 19h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
      <span>Импорт из Я.Музыки</span>
    </div>`;

    for (const pl of playlists) {
      const arts = (pl.artworks || []).slice(0, 4);
      let gridHTML = "";
      for (let i = 0; i < 4; i++) {
        if (arts[i]) {
          gridHTML += `<img src="${esc(arts[i])}" alt="" loading="lazy" onerror="this.outerHTML='<div class=\\'pl-art-empty\\'><svg viewBox=\\'0 0 24 24\\'><polygon points=\\'6,3 20,12 6,21\\'/></svg></div>'"/>`;
        } else {
          gridHTML += `<div class="pl-art-empty"><svg viewBox="0 0 24 24"><polygon points="6,3 20,12 6,21"/></svg></div>`;
        }
      }
      html += `
        <div class="pl-card" data-plid="${esc(pl.id)}">
          <div class="pl-art-grid">${gridHTML}</div>
          <div class="pl-card-name">${esc(pl.name)}</div>
          <div class="pl-card-count">${pl.track_count || 0} треков</div>
        </div>`;
    }

    html += "</div>";
    $list.innerHTML = html;

    $("pl-create-btn")?.addEventListener("click", () => openModal($modalCreatePl));
    $("pl-import-ym-btn")?.addEventListener("click", () => {
      if ($modalImportYM) openModal($modalImportYM);
    });

    $list.querySelectorAll(".pl-card").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (e.target.closest(".pl-delete")) return;
        openPlaylistDetail(card.dataset.plid);
      });
    });
  }

  async function openPlaylistDetail(plId) {
    showShimmer();
    try {
      const data = await api("GET", `/api/playlists/${plId}`);
      currentPlaylistDetail = data.playlist;
      renderPlaylistDetail(currentPlaylistDetail);
    } catch (e) {
      showStatus("❌", `Не удалось загрузить плейлист<br><small>${esc(e.message)}</small>`);
    }
  }

  function openRenamePlModal(plId, currentName) {
    _renamePlId = plId;
    $renamePlName.value = currentName || "";
    $renamePlSubmit.disabled = !currentName?.trim();
    openModal($modalRenamePl);
    setTimeout(() => $renamePlName.focus(), 150);
  }

  $renamePlName.addEventListener("input", () => {
    $renamePlSubmit.disabled = !$renamePlName.value.trim();
  });

  $renamePlSubmit.addEventListener("click", async () => {
    const name = $renamePlName.value.trim();
    if (!name || !_renamePlId) return;
    $renamePlSubmit.disabled = true;
    try {
      await api("PATCH", `/api/playlists/${_renamePlId}`, { name });
      toast("✏️", `Плейлист переименован в «${name}»`);
      closeModal($modalRenamePl);
      await loadPlaylists();
      if (currentPlaylistDetail && currentPlaylistDetail.id === _renamePlId) {
        currentPlaylistDetail.name = name;
        renderPlaylistDetail(currentPlaylistDetail);
      }
      _renamePlId = null;
    } catch (e) {
      toast("❌", e.message || "Ошибка переименования");
    } finally {
      $renamePlSubmit.disabled = false;
    }
  });

  $modalRenamePl?.addEventListener("click", (e) => {
    if (e.target === $modalRenamePl) closeModal($modalRenamePl);
  });

  function renderPlaylistDetail(pl) {
    const tracks = pl.tracks || [];
    let headerHTML = `
      <div class="pl-detail-header">
        <button class="pl-back-btn" id="pl-back">
          <svg viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <div class="pl-detail-info">
          <div class="pl-detail-name">${esc(pl.name)}</div>
          <div class="pl-detail-count">${tracks.length} треков</div>
        </div>
        <button class="pl-edit-btn" id="pl-edit-btn" data-plid="${esc(pl.id)}" title="Переименовать">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
        <div class="pl-detail-actions">
          <button class="pl-delete-btn" id="pl-delete-btn" data-plid="${esc(pl.id)}" title="Удалить плейлист">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
          </button>
          ${tracks.length ? `
            <button class="pl-shuffle-btn" id="pl-shuffle-btn" title="Перемешать">
              <svg viewBox="0 0 24 24"><path d="M16 3h5v5M4 20L20.2 3.8M21 16v5h-5M15 15l5.1 5.1M4 4l5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
            <button class="pl-play-all" id="pl-play-all">▶</button>
          ` : ""}
        </div>
      </div>`;

    if (!tracks.length) {
      $list.innerHTML = headerHTML + `<div class="status"><span class="status-emoji">📭</span>Плейлист пуст<br><small>Добавляй треки кнопкой + рядом с треком</small></div>`;
    } else {
      $list._tracks = tracks;
      $list._source = "playlist";

      const tracksHTML = tracks.map((t, i) => {
        const active = cur && String(cur.id) === String(t.id);
        const src = t.source || "soundcloud";
        return `
          <article class="track${active ? " playing" : ""}" data-id="${esc(String(t.id))}" data-idx="${i}">
            <div class="t-art-wrap">
              <img class="t-art" src="${esc(artUrl(t))}" alt="" loading="lazy"/>
              ${sourceBadgeHTML(src)}
              <div class="t-play-btn">${active && isPlaying ? eqHTML(false) : '<svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg>'}</div>
            </div>
            <div class="t-meta">
              <div class="t-name">${esc(t.title)}</div>
              <div class="t-artist">${esc(t.artist)}</div>
              <div class="t-info-row">
                <span class="t-dur">${esc(t.duration || "0:00")}</span>
                ${sourceTagHTML(src)}
              </div>
            </div>
            <div class="t-side">
              <button class="t-fav${isFav(t.id) ? " active" : ""}" data-id="${esc(String(t.id))}" title="В избранное">
                <svg viewBox="0 0 24 24"><path d="M12 21s-6.716-4.35-9.193-8.091C.812 9.824 2.002 5.5 5.9 4.427 8.161 3.805 10.112 4.75 12 6.7c1.888-1.95 3.839-2.895 6.1-2.273 3.898 1.073 5.088 5.397 3.093 8.482C18.716 16.65 12 21 12 21z"/></svg>
              </button>
              <button class="t-remove-pl" data-id="${esc(String(t.id))}" data-plid="${esc(pl.id)}" title="Убрать из плейлиста">
                <svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><line x1="10" y1="11" x2="10" y2="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="14" y1="11" x2="14" y2="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
              </button>
              <button class="t-action" data-id="${esc(String(t.id))}" aria-label="Play">
                ${active && isPlaying ? eqHTML(false) : '<svg viewBox="0 0 24 24"><polygon points="6,3 20,12 6,21"/></svg>'}
              </button>
            </div>
          </article>`;
      }).join("");
    }

      $list.innerHTML = headerHTML + tracksHTML;
      bindTrackEvents();

      $("pl-delete-btn")?.addEventListener("click", async () => {
        const plid = $("pl-delete-btn").dataset.plid;
        const ok = await confirmDialog({
          icon: "🗑️",
          title: "Удалить плейлист?",
          text: `Плейлист <b>«${esc(pl.name)}»</b> и все треки в нём будут удалены.`,
          okText: "Удалить",
        });
        if (!ok) return;
        try {
          await api("DELETE", `/api/playlists/${plid}`);
          toast("🗑️", "Плейлист удалён");
          currentPlaylistDetail = null;
          await loadPlaylists();
          switchTab("playlists");
        } catch (err) {
          toast("❌", "Ошибка удаления");
        }
      });

      $list.querySelectorAll(".t-remove-pl").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          const trackEl = btn.closest(".track");
          const idx = Number(trackEl.dataset.idx);
          const trackName = $list._tracks[idx]?.title || "трек";
          const ok = await confirmDialog({
            icon: "🎵",
            title: "Убрать из плейлиста?",
            text: `<b>«${esc(trackName)}»</b> будет убран из плейлиста.`,
            okText: "Убрать",
          });
          if (!ok) return;
          try {
            await api("DELETE", `/api/playlists/${btn.dataset.plid}/tracks/${btn.dataset.id}`);
            toast("✅", "Трек убран");
            await openPlaylistDetail(btn.dataset.plid);
          } catch (err) {
            toast("❌", "Ошибка");
          }
        });
      });

    $("pl-back")?.addEventListener("click", () => {
      currentPlaylistDetail = null;
      loadPlaylists();
    });

    // ✏️ Переименовать
    $("pl-edit-btn")?.addEventListener("click", () => {
      openRenamePlModal(pl.id, pl.name);
    });

    $("pl-play-all")?.addEventListener("click", () => {
      if (!tracks.length) return;
      setQueue(tracks, 0);
      playTrack(tracks[0]);
    });

    $("pl-shuffle-btn")?.addEventListener("click", () => {
      if (!tracks.length) return;
      const shuffled = shuffleArray(tracks);
      setQueue(shuffled, 0);
      playTrack(shuffled[0]);
      toast("🔀", "Перемешано");
    });
  }

  // ── Modals ──
  function openModal(modal) {
    modal.classList.add("open");
  }

  function closeModal(modal) {
    modal.classList.remove("open");
  }

  function confirmDialog({ icon = "🗑️", title = "Удалить?", text = "", okText = "Удалить", okClass = "confirm-danger" } = {}) {
    return new Promise((resolve) => {
      $confirmIcon.textContent = icon;
      $confirmTitle.textContent = title;
      $confirmText.innerHTML = text;
      $confirmOk.textContent = okText;
      $confirmOk.className = `confirm-btn ${okClass}`;
      _confirmResolve = resolve;
      openModal($modalConfirm);
    });
  }

  $confirmCancel.addEventListener("click", () => {
    closeModal($modalConfirm);
    if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
  });

  $confirmOk.addEventListener("click", () => {
    closeModal($modalConfirm);
    if (_confirmResolve) { _confirmResolve(true); _confirmResolve = null; }
  });

  $modalConfirm.addEventListener("click", (e) => {
    if (e.target === $modalConfirm) {
      closeModal($modalConfirm);
      if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
    }
  });

  function openAddToPlaylistModal() {
    if (!playlists.length) {
      toast("📁", "Сначала создайте плейлист");
      openModal($modalCreatePl);
      return;
    }
    $modalPlList.innerHTML = playlists.map((pl) => `
      <div class="modal-pl-item" data-plid="${esc(pl.id)}">
        <div class="modal-pl-icon">📁</div>
        <div class="modal-pl-name">${esc(pl.name)}</div>
        <div class="modal-pl-cnt">${pl.track_count || 0} треков</div>
      </div>
    `).join("");

    $modalPlList.querySelectorAll(".modal-pl-item").forEach((item) => {
      item.addEventListener("click", async () => {
        if (!pendingTrackForPlaylist) return;
        try {
          await api("POST", `/api/playlists/${item.dataset.plid}/tracks`, { track_id: pendingTrackForPlaylist.id });
          toast("✅", `Добавлено в «${playlists.find(p => p.id === item.dataset.plid)?.name || "плейлист"}»`);
          closeModal($modalAddToPl);
          pendingTrackForPlaylist = null;
          await loadPlaylists();
        } catch (e) {
          toast("❌", e.message || "Ошибка");
        }
      });
    });

    openModal($modalAddToPl);
  }

    // ── Yandex Music import (client beta flow) ──
  function setYMStatus(message, tone = "muted") {
    const colors = {
      muted: "var(--muted)",
      ok: "#86efac",
      err: "#fda4af",
      info: "var(--text)",
    };
    $importYMStatus.innerHTML = `<div style="color:${colors[tone] || colors.muted};line-height:1.45;">${message}</div>`;
  }

  function renderYMProgress(current, total, imported, trackName) {
    const pct = total > 0 ? Math.max(3, Math.round((current / total) * 100)) : 8;
    $importYMStatus.innerHTML = `
      <div style="margin-bottom:8px;color:var(--text);font-weight:700;">Импортирую треки: ${current} / ${total}</div>
      <div style="height:6px;background:rgba(255,255,255,.08);border-radius:99px;overflow:hidden;">
        <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,#7c3aed,#06b6d4);transition:width .25s ease;"></div>
      </div>
      <div style="margin-top:8px;font-size:12px;color:var(--muted);">Найдено: ${imported} ${trackName ? `• ${esc(trackName)}` : ""}</div>
    `;
  }

  function cleanYMUrl(url) {
    return String(url || "").trim().replace(/[?#].*$/, "");
  }

  function parseYMClassicUrl(url) {
    const m = cleanYMUrl(url).match(/music\.yandex\.(?:ru|com)\/users\/([^/]+)\/playlists\/(\d+)/i);
    return m ? { owner: m[1], kind: m[2] } : null;
  }

  function safeJSONParse(raw) {
    try { return JSON.parse(raw); } catch (_) { return null; }
  }

  function findPlaylistData(node, depth = 0) {
    if (!node || depth > 12) return null;
    if (Array.isArray(node)) {
      for (const item of node) {
        const found = findPlaylistData(item, depth + 1);
        if (found) return found;
      }
      return null;
    }
    if (typeof node !== "object") return null;

    const tracks = node.tracks || node.trackIds || node.items;
    if (node.title && tracks && (Array.isArray(tracks) || typeof tracks === "object")) {
      return node;
    }

    for (const key of Object.keys(node)) {
      const found = findPlaylistData(node[key], depth + 1);
      if (found) return found;
    }
    return null;
  }

  function collectYMTrackObjects(node, out = [], depth = 0) {
    if (!node || depth > 14) return out;
    if (Array.isArray(node)) {
      for (const item of node) collectYMTrackObjects(item, out, depth + 1);
      return out;
    }
    if (typeof node !== "object") return out;

    const hasTitle = typeof node.title === "string" && node.title.trim();
    const hasArtist = typeof node.artist === "string" || Array.isArray(node.artists) || node.artistsName || node.subtitle;
    const hasDuration = node.durationMs || node.duration || node.durationSec;
    if (hasTitle && hasArtist && hasDuration) out.push(node);

    for (const key of Object.keys(node)) collectYMTrackObjects(node[key], out, depth + 1);
    return out;
  }

  function normalizeYMTrack(raw) {
    const title = String(raw?.title || "").trim();
    if (!title) return null;

    let artist = "";
    if (typeof raw.artist === "string") artist = raw.artist;
    else if (typeof raw.artistsName === "string") artist = raw.artistsName;
    else if (typeof raw.subtitle === "string") artist = raw.subtitle;
    else if (Array.isArray(raw.artists)) {
      artist = raw.artists.map((a) => a?.name || a?.title || "").filter(Boolean).join(", ");
    }
    artist = String(artist || "").trim();
    if (!artist) return null;

    const durationMs = Number(raw.durationMs || 0);
    const duration = Number(raw.duration || 0);
    const durationSec = Number(raw.durationSec || 0) || (durationMs > 0 ? Math.round(durationMs / 1000) : (duration > 0 ? Math.round(duration) : 0));

    return {
      title,
      artist,
      duration_sec: durationSec,
    };
  }

  function extractYMPlaylistFromHTML(html, sourceUrl) {
    const titleMatch = html.match(/<title>([^<]+)<\/title>/i);
    const pageTitle = titleMatch ? titleMatch[1].replace(/\s*[—|-]\s*Яндекс Музыка.*$/i, "").trim() : "Яндекс Музыка";

    const patterns = [
      /window\.__INITIAL_STATE__\s*=\s*(\{[\s\S]+?\})\s*;<\/script>/i,
      /window\.__NEXT_DATA__\s*=\s*(\{[\s\S]+?\})\s*;<\/script>/i,
      /var\s+Mu\s*=\s*(\{[\s\S]+?\})\s*;<\/script>/i,
    ];

    let root = null;
    for (const re of patterns) {
      const m = html.match(re);
      if (!m) continue;
      root = safeJSONParse(m[1]);
      if (root) break;
    }

    let playlistNode = root ? findPlaylistData(root) : null;
    let candidates = [];
    if (playlistNode) candidates = collectYMTrackObjects(playlistNode);
    if (!candidates.length && root) candidates = collectYMTrackObjects(root);

    if (!candidates.length) {
      const fallbackRe = /"title"\s*:\s*"([^"]+)"[\s\S]{0,220}?"artists"\s*:\s*\[(.*?)\][\s\S]{0,220}?(?:"durationMs"\s*:\s*(\d+)|"duration"\s*:\s*(\d+))/g;
      let m;
      while ((m = fallbackRe.exec(html)) !== null) {
        const artistNames = [...m[2].matchAll(/"name"\s*:\s*"([^"]+)"/g)].map((x) => x[1]).join(", ");
        candidates.push({ title: m[1], artists: artistNames ? artistNames.split(/,\s*/) .map((name) => ({ name })) : [], durationMs: Number(m[3] || 0), duration: Number(m[4] || 0) });
      }
    }

    const dedup = new Map();
    for (const raw of candidates) {
      const track = normalizeYMTrack(raw);
      if (!track) continue;
      const key = `${track.artist}__${track.title}`.toLowerCase();
      if (!dedup.has(key)) dedup.set(key, track);
    }

    const tracks = [...dedup.values()];
    const name = String(playlistNode?.title || pageTitle || "Яндекс Музыка").trim();

    return { name, tracks, url: cleanYMUrl(sourceUrl) };
  }

  async function fetchYandexPlaylistClient(url) {
    const cleanUrl = cleanYMUrl(url);
    const res = await fetch(cleanUrl, {
      method: "GET",
      credentials: "include",
      headers: { "Accept": "text/html,application/xhtml+xml" },
    });

    const html = await res.text();
    const finalUrl = res.url || cleanUrl;

    if (res.redirected && /showcaptcha/i.test(finalUrl)) {
      throw new Error("Яндекс прислал captcha в клиенте. Открой этот плейлист в браузере и попробуй позже.");
    }
    if (/showcaptcha|smartcaptcha|form-fb-hint/i.test(finalUrl + "\n" + html.slice(0, 1500))) {
      throw new Error("Яндекс заблокировал чтение плейлиста captcha-защитой.");
    }
    if (!res.ok) {
      throw new Error(`Яндекс вернул ${res.status}`);
    }

    const payload = extractYMPlaylistFromHTML(html, cleanUrl);
    if (!payload.tracks.length) {
      throw new Error("Не удалось вытащить треки из HTML плейлиста. Возможно, нужен другой формат страницы или логин в Яндексе.");
    }
    return payload;
  }

  async function importYandexClient(url, payload) {
    const res = await fetch("/api/import/yandex/client", {
      method: "POST",
      headers: {
        "X-Init-Data": INIT_DATA,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        url: cleanYMUrl(url),
        name: payload.name,
        tracks: payload.tracks,
      }),
    });

    if (!res.ok) {
      throw new Error(await res.text().catch(() => `HTTP ${res.status}`));
    }
    if (!res.body) {
      throw new Error("Сервер не прислал поток импорта");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";

      for (const part of parts) {
        const dataLine = part.split("\n").find((line) => line.startsWith("data: "));
        if (!dataLine) continue;
        let evt;
        try {
          evt = JSON.parse(dataLine.slice(6));
        } catch (_) {
          continue;
        }

        if (evt.type === "start") {
          setYMStatus(`Нашел <b>${evt.total}</b> треков. Начинаю матчинг...`, "info");
        } else if (evt.type === "progress") {
          renderYMProgress(evt.current || 0, evt.total || 0, evt.imported || 0, evt.track || "");
        } else if (evt.type === "done") {
          setYMStatus(`✅ Готово: импортировано <b>${evt.imported}</b> из <b>${evt.total}</b> треков`, "ok");
          toast("✅", `Плейлист «${evt.name}» импортирован`);
          await loadPlaylists();
          setTimeout(() => closeModal($modalImportYM), 1800);
        }
      }
    }
  }

  if ($importYMUrl && $importYMSubmit && $importYMStatus) {
    $importYMUrl.addEventListener("input", () => {
      $importYMSubmit.disabled = !$importYMUrl.value.trim();
      if (!$importYMUrl.value.trim()) setYMStatus("");
    });

    $importYMSubmit.addEventListener("click", async () => {
      const url = $importYMUrl.value.trim();
      if (!url) return;

      $importYMSubmit.disabled = true;
      setYMStatus("⏳ Читаю страницу Яндекс Музыки в Mini App...", "info");

      try {
        const payload = await fetchYandexPlaylistClient(url);
        setYMStatus(`Нашел плейлист <b>${esc(payload.name)}</b> и <b>${payload.tracks.length}</b> треков. Отправляю на импорт...`, "info");
        await importYandexClient(url, payload);
      } catch (e) {
        console.error("yandex import beta", e);
        setYMStatus(`❌ ${esc(e.message || "Ошибка импорта")}`, "err");
      } finally {
        $importYMSubmit.disabled = false;
      }
    });
  }

  if ($modalImportYM) {
    $modalImportYM.addEventListener("click", (e) => {
      if (e.target === $modalImportYM) closeModal($modalImportYM);
    });
  }

  // Modal close on overlay click
   [$modalCreatePl, $modalAddToPl, $modalImportYM].filter(Boolean).forEach((modal) => {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal(modal);
    });
  });

  // Create playlist modal
  $newPlName.addEventListener("input", () => {
    $newPlSubmit.disabled = !$newPlName.value.trim();
  });

  $newPlSubmit.addEventListener("click", async () => {
    const name = $newPlName.value.trim();
    if (!name) return;
    $newPlSubmit.disabled = true;
    try {
      const data = await api("POST", "/api/playlists", { name });
      toast("✅", `Плейлист «${name}» создан`);
      $newPlName.value = "";
      closeModal($modalCreatePl);
      await loadPlaylists();

      // ── NEW: автоматически добавить трек, если нажали "+" ──
      if (pendingTrackForPlaylist && data.id) {
        try {
          await api("POST", `/api/playlists/${data.id}/tracks`, {
            track_id: pendingTrackForPlaylist.id,
          });
          toast("🎵", `Трек добавлен в «${name}»`);
          await loadPlaylists();
        } catch (e) {
          toast("❌", "Плейлист создан, но трек не добавлен");
        }
        pendingTrackForPlaylist = null;
      }
    } catch (e) {
      toast("❌", e.message || "Ошибка создания");
    } finally {
      $newPlSubmit.disabled = false;
    }
  });

  // ── Source chips ──
  document.querySelectorAll(".source-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      activeSource = chip.dataset.source;
      document.querySelectorAll(".source-chip").forEach((c) => c.classList.toggle("active", c.dataset.source === activeSource));
      const query = $q.value.trim();
      if (query.length >= 2) {
        clearTimeout(searchTimer);
        doSearch(query);
      }
    });
  });

  // ── Search input ──
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

  // ── Tab clicks ──
  $tabSearch.addEventListener("click", () => switchTab("search"));
  $tabPlaylists.addEventListener("click", () => switchTab("playlists"));
  $tabFav.addEventListener("click", () => switchTab("favorites"));

  // ── Player controls ──
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
      if (audio.paused) audio.play().catch(console.error);
    }, PREV_DOUBLE_TAP_MS);
  });

  $btnNext.addEventListener("click", () => playNext());

  $btnShuffle.addEventListener("click", () => {
    shuffleMode = !shuffleMode;
    $btnShuffle.classList.toggle("mode-active", shuffleMode);
    haptic("light");
    toast(shuffleMode ? "🔀" : "➡️", shuffleMode ? "Перемешивание вкл" : "Перемешивание выкл");
  });

  $btnRepeat.addEventListener("click", () => {
    if (repeatMode === "none") repeatMode = "all";
    else if (repeatMode === "all") repeatMode = "one";
    else repeatMode = "none";

    $btnRepeat.classList.toggle("mode-active", repeatMode !== "none");

    // Show "1" indicator for repeat one
    if (repeatMode === "one") {
      $btnRepeat.style.position = "relative";
      $btnRepeat.innerHTML = `
        <svg viewBox="0 0 24 24"><path d="M17 1l4 4-4 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M3 11V9a4 4 0 0 1 4-4h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M7 23l-4-4 4-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 13v2a4 4 0 0 1-4 4H3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span style="position:absolute;bottom:2px;right:4px;font-size:9px;font-weight:900;color:var(--accent-2);">1</span>`;
    } else {
      $btnRepeat.innerHTML = `<svg viewBox="0 0 24 24"><path d="M17 1l4 4-4 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M3 11V9a4 4 0 0 1 4-4h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M7 23l-4-4 4-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M21 13v2a4 4 0 0 1-4 4H3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    }

    haptic("light");
    const labels = { none: "Повтор выкл", all: "Повтор всех", one: "Повтор одного" };
    const emojis = { none: "➡️", all: "🔁", one: "🔂" };
    toast(emojis[repeatMode], labels[repeatMode]);
  });

  $btnFav.addEventListener("click", async () => { if (cur) await toggleFavorite(cur); });

  $progressHit.addEventListener("click", (e) => {
    if (!audio.duration) return;
    const bar = e.currentTarget.querySelector(".player-progress-track");
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
      toast("🎶", "Трек отправлен в чат");
    } catch (e) {
      console.error(e);
      $btnDL.classList.add("err");
      toast("❌", "Ошибка отправки");
    } finally {
      $btnDL.classList.remove("loading");
      setTimeout(() => $btnDL.classList.remove("ok", "err"), 2400);
    }
  });

  // ── Audio events ──
  audio.addEventListener("timeupdate", () => {
    if (!audio.duration) return;
    $pBar.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
    $pCur.textContent = fmtTime(audio.currentTime);
  });
  audio.addEventListener("loadedmetadata", () => { $pTotal.textContent = fmtTime(audio.duration); });
  audio.addEventListener("play", () => { setPlaying(true); syncTrackList(); });
  audio.addEventListener("pause", () => { setPlaying(false); syncTrackList(); });
  audio.addEventListener("ended", () => playNext());

  document.addEventListener("touchstart", (e) => {
    if (
      e.target.closest("input") ||
      e.target.closest("textarea") ||
      e.target.closest("[contenteditable]") ||
      e.target.closest(".modal-sheet") ||
      e.target.closest(".search-wrap")
    ) return;
    hideKeyboard();
  }, { passive: true });

  let _scrollBlurTimer = null;
  window.addEventListener("scroll", () => {
    // Не закрываем клавиатуру если открыта модалка
    if (document.querySelector(".modal-overlay.open")) return;
    clearTimeout(_scrollBlurTimer);
    _scrollBlurTimer = setTimeout(hideKeyboard, 60);
  }, { passive: true });

  document.addEventListener("click", (e) => {
    const target = e.target;
    if (
      target.closest("input") ||
      target.closest("textarea") ||
      target.closest(".search-wrap") ||
      target.closest(".modal-input")
    ) {
      return;
    }
    hideKeyboard();
  });

  let keyboardScrollTimer = null;

  window.addEventListener("scroll", () => {
    clearTimeout(keyboardScrollTimer);
    keyboardScrollTimer = setTimeout(() => {
      hideKeyboard();
    }, 30);
  }, { passive: true });

  // ── Init ──
  loadFavorites();
  loadPlaylists();
  if (!HAS_TG) setTimeout(() => $q.focus(), 250);
  