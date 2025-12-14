function setStatus(el, text, type = "info") {
  if (!el) return;
  el.textContent = text;
  el.classList.remove("hidden", "error", "info");
  el.classList.add(type);
}

/* ============================================================
   ANALISIS GAMBAR
============================================================ */

const imageForm = document.getElementById("image-form");
const imageInput = document.getElementById("image-input");
const imageStatus = document.getElementById("image-status");
const btnImage = document.getElementById("btn-image");

const previewOriginal = document.getElementById("preview-original");
const previewAnnotated = document.getElementById("preview-annotated");
const summaryBox = document.getElementById("summary-box");
const fishTableBody = document.querySelector("#fish-table tbody");
const csvLink = document.getElementById("csv-link");

// tombol feed (image page)
const btnFeed = document.getElementById("btn-feed");
const feedStatus = document.getElementById("feed-status");

if (imageForm) {
  imageForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const form = new FormData();
    form.append("image", imageInput.files[0]);

    previewOriginal.src = URL.createObjectURL(imageInput.files[0]);
    previewAnnotated.src = "";

    setStatus(imageStatus, "Memproses gambar...", "info");
    btnImage.disabled = true;

    const resp = await fetch("/api/analyze-image", { method: "POST", body: form });
    const data = await resp.json();
    btnImage.disabled = false;

    if (data.status !== "ok") {
      setStatus(imageStatus, data.message || "Gagal analisis.", "error");
      return;
    }

    previewAnnotated.src = data.image_url + "?v=" + Date.now();
    csvLink.href = data.csv_url;

    const s = data.summary;
    summaryBox.innerHTML = `
      <p><strong>Run ID:</strong> ${s.run_id}</p>
      <p><strong>Jumlah ikan:</strong> ${s.num_fish}</p>
      <p><strong>Rata-rata panjang:</strong> ${s.avg_length_cm.toFixed(2)} cm</p>
      <p><strong>Status panen:</strong> ${s.harvest_status}</p>
      <p><strong>Putaran Pakan Otomatis:</strong> ${s.feeding_turns}x</p>
      <p><strong>Durasi pakan:</strong> ${s.feeding_duration_ms} ms</p>
    `;
    summaryBox.classList.remove("muted");

    fishTableBody.innerHTML = "";
    data.records.forEach((r) => {
      const row = `
        <tr>
          <td>${r.fish_id}</td>
          <td>${r.confidence.toFixed(3)}</td>
          <td>${r.length_cm.toFixed(2)}</td>
        </tr>`;
      fishTableBody.insertAdjacentHTML("beforeend", row);
    });

    setStatus(imageStatus, "Analisis selesai.", "info");

    // aktifkan tombol feed setelah ada hasil
    if (btnFeed) btnFeed.disabled = false;
  });
}

/* ============================================================
   FEED OTOMATIS - IMAGE PAGE
============================================================ */

if (btnFeed) {
  btnFeed.disabled = true;

  btnFeed.addEventListener("click", async () => {
    // Kirim payload kosong. Backend akan menggunakan LAST_SUMMARY.feeding_turns
    feedStatus.textContent = "Mengirim perintah pakan otomatis...";
    feedStatus.style.color = "#0f172a";

    const resp = await fetch("/api/feed-manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });

    const data = await resp.json();

    if (data.status !== "ok") {
      feedStatus.textContent = data.message || "Gagal mengirim pakan.";
      feedStatus.style.color = "red";
      return;
    }

    feedStatus.textContent = `Pakan otomatis dikirim (${data.turns}x putaran servo)`;
    feedStatus.style.color = "green";
  });
}



/* ============================================================
   ANALISIS VIDEO
============================================================ */

const videoForm = document.getElementById("video-form");
const videoInput = document.getElementById("video-input");
const videoStatus = document.getElementById("video-status");
const btnVideo = document.getElementById("btn-video");

const videoPreview = document.getElementById("video-preview");
const videoCsv = document.getElementById("video-csv");
const videoSummary = document.getElementById("video-summary");

// tombol feed (video page)
const btnFeedVideo = document.getElementById("btn-feed-video");
const feedStatusVideo = document.getElementById("feed-status-video");

if (videoForm) {
  if (btnFeedVideo) btnFeedVideo.disabled = true;

  videoForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const form = new FormData();
    form.append("video", videoInput.files[0]);

    setStatus(videoStatus, "Memproses video...", "info");
    btnVideo.disabled = true;

    const resp = await fetch("/api/analyze-video", { method: "POST", body: form });
    const data = await resp.json();
    btnVideo.disabled = false;

    if (data.status !== "ok") {
      setStatus(videoStatus, data.message || "Gagal analisis video.", "error");
      return;
    }

    videoPreview.src = data.video_url + "?v=" + Date.now();
    videoPreview.load();
    videoPreview.play().catch(() => {});

    videoCsv.href = data.csv_url;

    const s = data.summary;
    videoSummary.innerHTML = `
      <p><strong>Run ID:</strong> ${s.run_id}</p>
      <p><strong>Jumlah ikan:</strong> ${s.num_fish}</p>
      <p><strong>Rata-rata panjang:</strong> ${s.avg_length_cm.toFixed(2)} cm</p>
      <p><strong>Status panen:</strong> ${s.harvest_status}</p>
      <p><strong>Putaran Pakan Otomatis:</strong> ${s.feeding_turns}x</p>
      <p><strong>Durasi pakan:</strong> ${s.feeding_duration_ms} ms</p>
      <p><strong>Total log deteksi:</strong> ${data.total_logs}</p>
    `;

    setStatus(videoStatus, "Analisis video selesai.", "info");

    if (btnFeedVideo) btnFeedVideo.disabled = false;
  });
}

/* ============================================================
   FEED OTOMATIS - VIDEO PAGE
============================================================ */

if (btnFeedVideo) {
  btnFeedVideo.addEventListener("click", async () => {
    // Kirim payload kosong. Backend akan menggunakan LAST_SUMMARY.feeding_turns

    feedStatusVideo.textContent = "Mengirim perintah pakan otomatis...";
    feedStatusVideo.style.color = "#0f172a";

    const resp = await fetch("/api/feed-manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });

    const data = await resp.json();

    if (data.status !== "ok") {
      feedStatusVideo.textContent = data.message || "Gagal mengirim pakan.";
      feedStatusVideo.style.color = "red";
      return;
    }

    feedStatusVideo.textContent = `Pakan otomatis dikirim (${data.turns}x putaran servo)`;
    feedStatusVideo.style.color = "green";
  });
}

/* ============================================================
   STREAMING
============================================================ */

const streamImg = document.getElementById("stream-video");
const streamStatus = document.getElementById("stream-status");
const btnStreamCapture = document.getElementById("btn-stream-capture");
const btnStreamRecStart = document.getElementById("btn-stream-rec-start");
const btnStreamRecStop = document.getElementById("btn-stream-rec-stop");

if (streamImg) {
  btnStreamCapture?.addEventListener("click", async () => {
    if (streamStatus) streamStatus.textContent = "Menyimpan snapshot...";
    const resp = await fetch("/stream/capture", { method: "POST" });
    const data = await resp.json();

    if (data.status !== "ok") {
      if (streamStatus) streamStatus.textContent = data.message || "Gagal capture.";
      return;
    }

    if (streamStatus) streamStatus.textContent = "Snapshot disimpan: " + data.file;
  });

  btnStreamRecStart?.addEventListener("click", async () => {
    if (streamStatus) streamStatus.textContent = "Mengaktifkan perekaman...";
    const resp = await fetch("/stream/record-start", { method: "POST" });
    const data = await resp.json();

    if (data.status === "error") {
      if (streamStatus) streamStatus.textContent = data.message;
    } else if (data.status === "already_recording") {
      if (streamStatus) streamStatus.textContent = "Sedang merekam.";
    } else {
      if (streamStatus) streamStatus.textContent = "Rekaman dimulai: " + data.file;
    }
  });

  btnStreamRecStop?.addEventListener("click", async () => {
    const resp = await fetch("/stream/record-stop", { method: "POST" });
    const data = await resp.json();

    if (streamStatus) {
      streamStatus.textContent = (data.status === "ok")
        ? "Rekaman dihentikan."
        : "Tidak ada rekaman aktif.";
    }
  });
}