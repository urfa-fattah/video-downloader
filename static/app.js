(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    analysis: null, mode: "video", jobs: [], filter: "all",
    info: null, es: null, lastClip: "", lastFinished: -1,
  };

  /* ---------------------------------------------------------------- helpers */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const fmtBytes = (n) => {
    if (n == null) return "";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
  };
  const fmtTime = (s) => {
    if (s == null) return "";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(sec).padStart(2, "0")}`;
  };
  const fmtViews = (n) => n == null ? "" :
    n >= 1e9 ? (n / 1e9).toFixed(1) + "B views" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M views" :
    n >= 1e3 ? (n / 1e3).toFixed(1) + "K views" : n + " views";

  let toastTimer;
  const toast = (msg) => {
    const t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 3000);
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      method: opts.method || (opts.body ? "POST" : "GET"),
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status})`);
      err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  }

  /* ------------------------------------------------------------------ theme */
  $("themeBtn").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("grabbit-theme", next); } catch (e) { /* ignore */ }
  });

  /* ------------------------------------------------------------- info/pills */
  async function loadInfo() {
    try {
      state.info = await api("/api/info");
    } catch { return; }
    const i = state.info;
    $("statusPills").innerHTML = `
      <span class="pill ${i.ffmpeg ? "ok" : "warn"}" title="${i.ffmpeg ? "" : "Install FFmpeg for HD merge, MP3 and trim"}">FFmpeg ${i.ffmpeg ? "ready" : "missing"}</span>
      <span class="pill">yt-dlp ${esc(i.yt_dlp_version)}</span>`;
    applyFfmpegState();
    renderLibrary();
    updateIgNotice();
  }

  /* ----------------------------------------------- Instagram cookie warning */
  const hasCookies = () => {
    const s = (state.info && state.info.settings) || {};
    return !!(s.cookies_file || s.cookies_browser);
  };
  const isIgUrl = (t) => {
    try {
      const h = new URL(String(t).trim()).hostname.toLowerCase();
      return h.endsWith("instagram.com") || h === "instagr.am";
    } catch { return false; }
  };
  function updateIgNotice() {
    $("igNotice").hidden = !(isIgUrl($("urlInput").value) && !hasCookies());
  }
  $("urlInput").addEventListener("input", updateIgNotice);
  $("igSettingsBtn").addEventListener("click", () => openSettings());

  function applyFfmpegState() {
    const ok = state.info ? state.info.ffmpeg : true;
    ["trimStart", "trimEnd", "embedMeta", "embedThumb", "audioFormat"].forEach((id) => { $(id).disabled = !ok; });
    $("container").querySelector('option[value="mkv"]').disabled = !ok;
    $("audioModeBtn").textContent = ok ? "Audio (MP3 & more)" : "Audio (original)";
    $("ffmpegHint").hidden = ok;
  }

  function renderLibrary() {
    const done = state.jobs.filter((j) => j.status === "finished");
    const total = done.reduce((a, j) => a + j.files.reduce((b, f) => b + (f.size || 0), 0), 0);
    const free = state.info && state.info.disk_free != null ? ` · ${fmtBytes(state.info.disk_free)} free on disk` : "";
    $("library").textContent = done.length ? `${done.length} download${done.length > 1 ? "s" : ""} · ${fmtBytes(total)} saved${free}` : "";
  }

  /* ---------------------------------------------------------------- analyze */
  $("pasteBtn").addEventListener("click", async () => {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      if (text) { $("urlInput").value = text; $("urlInput").focus(); }
    } catch { toast("Clipboard access was blocked — paste with Ctrl+V."); }
  });

  $("urlForm").addEventListener("submit", (e) => { e.preventDefault(); analyze($("urlInput").value.trim()); });

  async function analyze(url) {
    if (!url) return;
    const btn = $("analyzeBtn");
    $("analyzeError").hidden = true;
    $("clipChip").hidden = true;
    updateIgNotice();
    btn.disabled = true; btn.textContent = "Analyzing…";
    try {
      const a = await api("/api/analyze", { body: { url } });
      state.analysis = a;
      renderPreview(a);
    } catch (err) {
      $("previewCard").hidden = true;
      $("analyzeError").textContent = err.message;
      $("analyzeError").hidden = false;
    } finally {
      btn.disabled = false; btn.textContent = "Analyze";
    }
  }

  function renderPreview(a) {
    $("previewCard").hidden = false;
    $("thumb").src = a.thumbnail || "";
    $("thumb").style.visibility = a.thumbnail ? "visible" : "hidden";
    $("duration").textContent = a.duration ? fmtTime(a.duration) : "";
    $("title").textContent = a.title;
    $("uploader").textContent = [a.uploader, a.upload_date, fmtViews(a.view_count)].filter(Boolean).join("  ·  ");
    $("badges").innerHTML =
      (a.extractor ? `<span class="badge">${esc(a.extractor)}</span>` : "") +
      (a.is_playlist ? `<span class="badge pl">Playlist</span>` : "") +
      (a.is_live ? `<span class="badge live">Live</span>` : "");
    $("liveNotice").hidden = !a.is_live;
    $("plHint").hidden = !a.playlist_url;

    // quality list with size estimates
    $("quality").innerHTML = `<option value="best">Best available</option>` +
      a.qualities.map((q) => {
        const tag = q.height >= 2160 ? " (4K)" : q.height >= 1440 ? " (2K)" : "";
        const size = q.size ? ` · ~${fmtBytes(q.size)}` : "";
        return `<option value="${q.height}">${q.height}p${tag}${size}</option>`;
      }).join("");

    // playlist-only controls
    const isPl = a.is_playlist;
    $("playlistToggle").hidden = !isPl;
    $("plItemsField").hidden = !isPl;
    $("skipArchiveRow").hidden = !isPl;
    $("playlist").checked = true;
    $("plItems").value = "";
    $("plCount").textContent = a.entry_count || 0;
    const list = $("plList");
    list.hidden = !isPl;
    list.innerHTML = isPl
      ? a.entries.map((e, i) => `<li><span>${i + 1}</span>${esc(e.title)}</li>`).join("") +
        (a.entry_count > a.entries.length ? `<li>…and ${a.entry_count - a.entries.length} more</li>` : "")
      : "";

    // subtitle language suggestions
    $("subLangList").innerHTML = (a.subtitle_langs || []).map((l) => `<option value="${esc(l)}"></option>`).join("");

    // defaults from settings
    const s = state.info ? state.info.settings : {};
    $("embedMeta").checked = !!s.embed_metadata;
    $("embedThumb").checked = !!s.embed_thumbnail;
    $("skipArchive").checked = !!s.skip_archive;
    $("trimStart").value = ""; $("trimEnd").value = "";
    $("subtitles").checked = false;
    setMode("video");
    applyFfmpegState();
    $("previewCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  $("loadPlaylistBtn").addEventListener("click", () => {
    const u = state.analysis && state.analysis.playlist_url;
    if (u) { $("urlInput").value = u; analyze(u); }
  });

  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    const audio = mode === "audio";
    $("qualityField").hidden = audio;
    $("containerField").hidden = audio;
    $("audioFmtField").hidden = !audio || (state.info && !state.info.ffmpeg);
  }
  document.querySelectorAll("#modeSeg button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

  /* --------------------------------------------------------------- download */
  function buildOptions() {
    const a = state.analysis;
    return {
      mode: state.mode,
      quality: state.mode === "video" ? $("quality").value : "best",
      container: $("container").value,
      audio_format: $("audioFormat").value,
      playlist: !!(a && a.is_playlist && $("playlist").checked),
      playlist_items: $("plItems").value.trim(),
      subtitles: $("subtitles").checked,
      sub_langs: $("subLangs").value.trim() || "en",
      trim_start: $("trimStart").value.trim(),
      trim_end: $("trimEnd").value.trim(),
      embed_metadata: $("embedMeta").checked,
      embed_thumbnail: $("embedThumb").checked,
      skip_archive: $("skipArchive").checked,
    };
  }

  async function submitJob(force) {
    const a = state.analysis;
    return api("/api/download", {
      body: { url: a.url, title: a.title, thumb: a.thumbnail || "", options: buildOptions(), force },
    });
  }

  $("downloadBtn").addEventListener("click", async () => {
    if (!state.analysis) return;
    const btn = $("downloadBtn");
    btn.disabled = true;
    try {
      try {
        await submitJob(false);
      } catch (err) {
        if (err.status === 409 && confirm(`${err.message}\n\nAdd it again anyway?`)) await submitJob(true);
        else throw err;
      }
      toast("Added to queue");
      $("queueList").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      if (err.status !== 409) toast(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  /* ------------------------------------------------------------------ batch */
  const batchLines = () => $("batchText").value.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  $("batchToggle").addEventListener("click", () => {
    const p = $("batchPanel");
    p.hidden = !p.hidden;
    $("batchToggle").textContent = p.hidden ? "+ Batch mode (many links)" : "− Hide batch mode";
    if (!p.hidden) $("batchText").focus();
  });
  $("batchText").addEventListener("input", () => {
    const n = batchLines().length;
    $("batchCount").textContent = n ? `· ${n} link${n > 1 ? "s" : ""}` : "";
  });
  $("batchBtn").addEventListener("click", async () => {
    const urls = batchLines();
    if (!urls.length) return toast("Paste at least one link.");
    const p = $("batchPreset").value;
    const options = p === "mp3" ? { mode: "audio", audio_format: "mp3" }
      : { mode: "video", quality: p, container: "mp4" };
    options.playlist = $("batchPlaylist").checked;
    const btn = $("batchBtn");
    btn.disabled = true;
    try {
      const r = await api("/api/batch", { body: { urls, options } });
      toast(`${r.created} added${r.errors.length ? `, ${r.errors.length} skipped` : ""}`);
      if (r.errors.length) {
        const bad = new Set(r.errors.map((e) => e.url));
        $("batchText").value = urls.filter((u) => bad.has(u)).join("\n");
      } else {
        $("batchText").value = "";
      }
      $("batchText").dispatchEvent(new Event("input"));
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  /* ------------------------------------------------------------------ queue */
  const FAILED = ["error", "cancelled", "interrupted"];
  const ACTIVE = ["queued", "downloading"];
  const STATUS_LABEL = {
    queued: "Queued", downloading: "Downloading", finished: "Completed",
    error: "Failed", cancelled: "Cancelled", interrupted: "Interrupted",
  };
  const matchFilter = (j) => state.filter === "all" ||
    (state.filter === "active" && ACTIVE.includes(j.status)) ||
    (state.filter === "done" && j.status === "finished") ||
    (state.filter === "failed" && FAILED.includes(j.status));

  function statsText(j) {
    if (j.status === "downloading" && !(j.stage || "").startsWith("Processing")) {
      const parts = [];
      if (j.percent) parts.push(`${j.percent.toFixed(1)}%`);
      if (j.speed) parts.push(`${fmtBytes(j.speed)}/s`);
      if (j.eta != null) parts.push(`ETA ${fmtTime(j.eta)}`);
      return parts.length ? parts.join(" · ") : (j.stage || "");
    }
    return j.stage || "";
  }
  const isIndeterminate = (j) => ACTIVE.includes(j.status) &&
    (j.status === "queued" || !j.percent || (j.stage || "").startsWith("Processing"));

  function modeTag(o) {
    if (o.mode === "audio") return o.audio_format.toUpperCase();
    return `${o.quality === "best" ? "Best" : o.quality + "p"} · ${o.container.toUpperCase()}`;
  }

  function jobHTML(j) {
    const o = j.options;
    const active = ACTIVE.includes(j.status);
    const trim = (o.trim_start != null || o.trim_end != null)
      ? `<span>✂ ${fmtTime(o.trim_start || 0)}–${o.trim_end != null ? fmtTime(o.trim_end) : "end"}</span>` : "";
    const thumb = j.thumb
      ? `<img class="job-thumb" src="${esc(j.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'">`
      : `<div class="job-thumb"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="4" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></svg></div>`;

    const files = (j.files || []).map((f, i) => `
      <div class="file-row">
        <span title="${esc(f.name)}">${esc(f.name)} · ${fmtBytes(f.size)}</span>
        <a href="/files/${encodeURIComponent(j.id)}/${i}" download>Save</a>
      </div>`).join("");

    let actions = "";
    if (active) {
      actions = `<button class="btn sm danger" data-act="cancel">Cancel</button>`;
    } else if (j.status === "finished") {
      actions = `${(j.files || []).length > 1 ? `<a class="btn sm success" href="/files/${j.id}.zip">Download all (ZIP)</a>` : ""}
        ${(j.files || []).length ? `<button class="btn sm ghost" data-act="open">Show in folder</button>` : ""}
        <button class="btn sm ghost" data-act="remove">Remove</button>
        ${(j.files || []).length ? `<button class="btn sm danger" data-act="delete">Delete files</button>` : ""}`;
    } else {
      const needsSettings = j.status === "error" && /Settings/.test(j.error || "");
      actions = `${needsSettings ? `<button class="btn sm ghost" data-act="settings">Open Settings</button>` : ""}
        <button class="btn sm warn" data-act="retry">${j.status === "interrupted" ? "Resume" : "Retry"}</button>
        <button class="btn sm ghost" data-act="remove">Remove</button>`;
    }

    const middle = j.status === "error"
      ? `<p class="job-error">${esc(j.error)}</p>`
      : `<div class="bar ${isIndeterminate(j) ? "indeterminate" : ""}"><i style="width:${j.status === "finished" ? 100 : j.percent || 0}%"></i></div>`;

    return `
    <article class="job ${j.status}" data-id="${j.id}">
      ${thumb}
      <div class="job-main">
        <div class="job-top">
          <div style="min-width:0">
            <div class="job-title" title="${esc(j.title)}">${esc(j.title)}</div>
            <div class="job-meta"><span>${esc(modeTag(o))}</span>${o.playlist ? "<span>Playlist</span>" : ""}${j.item ? `<span>${esc(j.item)}</span>` : ""}${trim}</div>
          </div>
          <span class="status ${j.status}">${STATUS_LABEL[j.status] || j.status}</span>
        </div>
        ${middle}
        <div class="job-foot">
          <div class="job-stats">${esc(statsText(j))}</div>
          <div class="job-actions">${actions}</div>
        </div>
        ${j.status === "finished" ? `<div class="files">${files}</div>` : ""}
      </div>
    </article>`;
  }

  // Everything except fast-changing progress numbers: if this is unchanged we only patch the bar.
  const structSig = (j) => JSON.stringify({ ...j, percent: 0, speed: 0, eta: 0, stage: ACTIVE.includes(j.status) ? "" : j.stage });

  function patchProgress(el, j) {
    const bar = el.querySelector(".bar");
    if (!bar) return;
    bar.classList.toggle("indeterminate", isIndeterminate(j));
    bar.firstElementChild.style.width = `${j.status === "finished" ? 100 : j.percent || 0}%`;
    el.querySelector(".job-stats").textContent = statsText(j);
  }

  function renderJobs() {
    const list = $("queueList");
    const visible = state.jobs.filter(matchFilter);
    const existing = new Map([...list.children].map((el) => [el.dataset.id, el]));

    visible.forEach((j, i) => {
      const sig = structSig(j);
      let el = existing.get(j.id);
      if (el && el.dataset.sig === sig) {
        patchProgress(el, j);
      } else {
        const t = document.createElement("template");
        t.innerHTML = jobHTML(j).trim();
        const fresh = t.content.firstElementChild;
        fresh.dataset.sig = sig;
        if (el) el.replaceWith(fresh);
        el = fresh;
      }
      existing.delete(j.id);
      if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null);
    });
    existing.forEach((el) => el.remove());

    $("emptyState").style.display = visible.length ? "none" : "";
    $("emptyText").textContent = state.jobs.length
      ? "Nothing here for this filter." : "No downloads yet. Paste a link above to get started.";

    const count = (fn) => state.jobs.filter(fn).length;
    $("cAll").textContent = state.jobs.length;
    $("cActive").textContent = count((j) => ACTIVE.includes(j.status));
    $("cDone").textContent = count((j) => j.status === "finished");
    $("cFailed").textContent = count((j) => FAILED.includes(j.status));
    renderLibrary();

    const finished = count((j) => j.status === "finished");
    if (finished !== state.lastFinished) {  // refresh free-disk number after a download completes
      if (state.lastFinished !== -1) loadInfo();
      state.lastFinished = finished;
    }
  }

  function setJobs(jobs) { state.jobs = jobs; renderJobs(); }

  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
    state.filter = b.dataset.f;
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    renderJobs();
  }));

  $("queueList").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const id = btn.closest(".job").dataset.id;
    const act = btn.dataset.act;
    try {
      if (act === "cancel") await api(`/api/jobs/${id}/cancel`, { method: "POST", body: {} });
      else if (act === "retry") await api(`/api/jobs/${id}/retry`, { method: "POST", body: {} });
      else if (act === "remove") await api(`/api/jobs/${id}`, { method: "DELETE" });
      else if (act === "delete") {
        if (!confirm("Delete the downloaded files from your disk too?")) return;
        await api(`/api/jobs/${id}?files=1`, { method: "DELETE" });
      } else if (act === "open") await api("/api/open-folder", { body: { job_id: id } });
      else if (act === "settings") openSettings();
    } catch (err) { toast(err.message); }
  });

  /* ------------------------------------------------------- live job updates */
  async function refreshJobs() {
    try { setJobs(await api("/api/jobs")); } catch { /* server restarting */ }
  }
  function connectStream() {
    if (!window.EventSource) { setInterval(refreshJobs, 1500); return; }
    const es = new EventSource("/api/stream");
    state.es = es;
    es.onmessage = (e) => { try { setJobs(JSON.parse(e.data)); } catch { /* ignore */ } };
    es.onerror = () => { refreshJobs(); };  // EventSource reconnects by itself
  }

  /* --------------------------------------------------------------- settings */
  const modal = $("settingsModal");
  function openSettings() {
    const s = (state.info && state.info.settings) || {};
    $("sOutput").value = s.output_dir || "";
    $("sParallel").value = s.max_parallel || 3;
    $("sSpeed").value = s.speed_limit_kbps || 0;
    $("sProxy").value = s.proxy || "";
    $("sBrowser").value = s.cookies_browser || "";
    $("sCookieFile").value = s.cookies_file || "";
    $("sMeta").checked = !!s.embed_metadata;
    $("sThumb").checked = !!s.embed_thumbnail;
    $("sArchive").checked = !!s.skip_archive;
    $("ytVer").textContent = state.info ? `v${state.info.yt_dlp_version}` : "";
    $("settingsError").hidden = true;
    renderCookieStatus();
    modal.hidden = false;
  }

  function renderCookieStatus(extra) {
    const s = (state.info && state.info.settings) || {};
    const el = $("cookieStatus");
    el.className = "muted-s";
    if (extra) {
      el.textContent = extra.text;
      if (extra.cls) el.classList.add(extra.cls);
    } else if (s.cookies_file) {
      el.textContent = `Using ${s.cookies_file.split(/[\\/]/).pop()}`;
    } else if (s.cookies_browser) {
      el.textContent = `Reading cookies from ${s.cookies_browser}`;
    } else {
      el.textContent = "No cookies set.";
    }
    $("cookieRemove").hidden = !s.cookies_file;
  }

  function applySettingsFromServer(s) {
    if (state.info) state.info.settings = s;
    $("sCookieFile").value = s.cookies_file || "";
    $("sBrowser").value = s.cookies_browser || "";
    updateIgNotice();
  }

  $("cookieUpload").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > 3 * 1024 * 1024) {
      return renderCookieStatus({ text: "That file is too large. Export cookies only for the site you need.", cls: "cookie-status-warn" });
    }
    renderCookieStatus({ text: "Checking file…" });
    try {
      const res = await fetch("/api/cookies", {
        method: "POST",
        headers: { "Content-Type": "text/plain", "X-Grabbit": "1" },
        body: await file.text(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Upload failed (${res.status})`);
      applySettingsFromServer(data.settings);
      if (data.instagram_logged_in) {
        renderCookieStatus({ text: `✓ ${data.count} cookies loaded — Instagram login found.`, cls: "cookie-status-ok" });
      } else if (data.instagram) {
        renderCookieStatus({ text: `${data.count} cookies loaded, but no Instagram login (sessionid). Log in to instagram.com, then export again.`, cls: "cookie-status-warn" });
      } else {
        renderCookieStatus({ text: `✓ ${data.count} cookies loaded (${data.domains.slice(0, 3).join(", ")}${data.domains.length > 3 ? "…" : ""}).`, cls: "cookie-status-ok" });
      }
      $("cookieRemove").hidden = false;
    } catch (err) {
      renderCookieStatus({ text: err.message, cls: "cookie-status-warn" });
    }
  });

  $("cookieRemove").addEventListener("click", async () => {
    try {
      const data = await api("/api/cookies", { method: "DELETE" });
      applySettingsFromServer(data.settings);
      renderCookieStatus();
      toast("Cookies removed");
    } catch (err) { toast(err.message); }
  });
  const closeSettings = () => { modal.hidden = true; };
  $("settingsBtn").addEventListener("click", openSettings);
  $("settingsClose").addEventListener("click", closeSettings);
  $("settingsCancel").addEventListener("click", closeSettings);
  modal.addEventListener("mousedown", (e) => { if (e.target === modal) closeSettings(); });

  $("settingsSave").addEventListener("click", async () => {
    const btn = $("settingsSave");
    btn.disabled = true;
    try {
      const saved = await api("/api/settings", { body: {
        output_dir: $("sOutput").value.trim(),
        max_parallel: Number($("sParallel").value),
        speed_limit_kbps: Number($("sSpeed").value || 0),
        proxy: $("sProxy").value.trim(),
        cookies_browser: $("sBrowser").value,
        cookies_file: $("sCookieFile").value.trim(),
        embed_metadata: $("sMeta").checked,
        embed_thumbnail: $("sThumb").checked,
        skip_archive: $("sArchive").checked,
      } });
      if (state.info) state.info.settings = saved;
      closeSettings();
      toast("Settings saved");
      loadInfo();
    } catch (err) {
      $("settingsError").textContent = err.message;
      $("settingsError").hidden = false;
    } finally {
      btn.disabled = false;
    }
  });

  $("updateBtn").addEventListener("click", async () => {
    const btn = $("updateBtn"), msg = $("updateMsg");
    btn.disabled = true; btn.textContent = "Updating…";
    msg.textContent = "Checking for a newer yt-dlp — this can take a minute…";
    try {
      const r = await api("/api/update-ytdlp", { method: "POST", body: {} });
      if (!r.ok) msg.textContent = `Update failed. ${r.log || ""}`;
      else if (r.restart_needed) msg.textContent = `Updated ${r.before} → ${r.after}. Close and restart the app to use it.`;
      else msg.textContent = `Already up to date (${r.after}).`;
    } catch (err) {
      msg.textContent = err.message;
    } finally {
      btn.disabled = false; btn.textContent = "Update yt-dlp";
    }
  });

  $("folderBtn").addEventListener("click", async () => {
    try { await api("/api/open-folder", { body: {} }); } catch (err) { toast(err.message); }
  });

  /* --------------------------------------------- clipboard chip, dnd, keys */
  const isUrl = (t) => /^https?:\/\/\S+$/i.test(t);
  async function checkClipboard() {
    try {
      const perm = await navigator.permissions.query({ name: "clipboard-read" });
      if (perm.state !== "granted") return;  // never trigger a permission prompt on our own
      const t = (await navigator.clipboard.readText()).trim();
      if (isUrl(t) && t !== $("urlInput").value && t !== state.lastClip) {
        state.lastClip = t;
        const chip = $("clipChip");
        let label = t;
        try { const u = new URL(t); label = u.hostname.replace(/^www\./, "") + (u.pathname.length > 1 ? u.pathname : ""); } catch { /* keep */ }
        chip.textContent = `📋 Link in clipboard: ${label.length > 48 ? label.slice(0, 47) + "…" : label}`;
        chip.dataset.url = t;
        chip.hidden = false;
      }
    } catch { /* unsupported browser */ }
  }
  $("clipChip").addEventListener("click", () => {
    const u = $("clipChip").dataset.url;
    $("clipChip").hidden = true;
    if (u) { $("urlInput").value = u; analyze(u); }
  });
  window.addEventListener("focus", checkClipboard);

  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    const t = (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "").trim().split(/\r?\n/)[0];
    if (isUrl(t)) { $("urlInput").value = t; analyze(t); }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) closeSettings();
    const tag = (e.target.tagName || "").toLowerCase();
    if (e.key === "/" && !["input", "textarea", "select"].includes(tag)) {
      e.preventDefault();
      $("urlInput").focus();
    }
  });

  /* ------------------------------------------------------------------- init */
  loadInfo();
  refreshJobs();
  connectStream();
  checkClipboard();
})();
