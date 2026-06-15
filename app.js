(() => {
  const TRACKS = [
    {
      id: "drums",
      label: "Bateria",
      accent: "cyan",
      color: "#06b6d4",
      shortName: "DRUMS",
    },
    {
      id: "bass",
      label: "Baixo",
      accent: "emerald",
      color: "#10b981",
      shortName: "BASS",
    },
    {
      id: "melody",
      label: "Melodia",
      accent: "amber",
      color: "#f59e0b",
      shortName: "LEAD",
    },
    {
      id: "voice",
      label: "Voz",
      accent: "pink",
      color: "#ec4899",
      shortName: "VOX",
    },
  ];

  const NOTE_TO_SEMITONE = {
    C: 0,
    "C#": 1,
    Db: 1,
    D: 2,
    "D#": 3,
    Eb: 3,
    E: 4,
    F: 5,
    "F#": 6,
    Gb: 6,
    G: 7,
    "G#": 8,
    Ab: 8,
    A: 9,
    "A#": 10,
    Bb: 10,
    B: 11,
  };

  const ACCEPTED_EXTENSIONS = ["mp3", "wav"];
  const DEFAULT_RENDER_SECONDS = 24;
  const SAMPLE_RATE = 44100;
  const BACKEND_URL = "http://localhost:8000/api/process-track";
  const TRACK_SIMULATED_METADATA = {
    drums: { originalBpm: 120, originalKeyId: 0 },
    bass: { originalBpm: 120, originalKeyId: 0 },
    melody: { originalBpm: 120, originalKeyId: 0 },
    voice: { originalBpm: 120, originalKeyId: 0 },
  };

  const state = {
    audioContext: null,
    masterGain: null,
    isPlaying: false,
    sessionStartTime: 0,
    renderBusy: false,
    tracks: new Map(),
  };

  const els = {
    bpmGlobal: document.getElementById("bpmGlobal"),
    bpmValue: document.getElementById("bpmValue"),
    keyGlobal: document.getElementById("keyGlobal"),
    playSession: document.getElementById("playSession"),
    pauseSession: document.getElementById("pauseSession"),
    stopSession: document.getElementById("stopSession"),
    renderMix: document.getElementById("renderMix"),
    sessionLed: document.getElementById("sessionLed"),
    sessionStatus: document.getElementById("sessionStatus"),
    footerSession: document.getElementById("footerSession"),
  };

  function init() {
    if (!window.AudioContext && !window.webkitAudioContext) {
      setStatus("Seu navegador não suporta Web Audio API", "error");
      return;
    }

    TRACKS.forEach((track) => {
      const panel = document.querySelector(`[data-track="${track.id}"]`);
      const dropzone = panel.querySelector("[data-dropzone]");
      const input = panel.querySelector(".track-input");
      const fileLabel = panel.querySelector("[data-file-label]");
      const volume = panel.querySelector(".track-volume");
      const volumeValue = panel.querySelector("[data-volume-value]");
      const muteButton = panel.querySelector("[data-mute]");
      const soloButton = panel.querySelector("[data-solo]");
      const clearButton = panel.querySelector("[data-clear]");
      const waveform = panel.querySelector("[data-waveform]");
      const trackState = panel.querySelector("[data-track-state]");

      const model = {
        ...track,
        panel,
        dropzone,
        input,
        fileLabel,
        volume,
        volumeValue,
        muteButton,
        soloButton,
        clearButton,
        waveform,
        trackState,
        file: null,
        buffer: null,
        source: null,
        gainNode: null,
        mute: false,
        solo: false,
        userVolume: Number(volume.value),
        processing: false,
        requestController: null,
        processedBpm: null,
        processedKeyId: null,
        bars: [],
      };

      model.gainNode = getAudioContext().createGain();
      model.gainNode.gain.value = model.userVolume;
      model.gainNode.connect(getMasterGain());

      createWaveBars(model);
      bindTrackEvents(model);
      state.tracks.set(track.id, model);
      refreshTrackUi(model);
    });

    bindGlobalEvents();
    updateSessionUi();
    setStatus("Pronto para carregar stems", "ready");
  }

  function getAudioContext() {
    if (!state.audioContext) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      state.audioContext = new Ctor();
      state.masterGain = state.audioContext.createGain();
      state.masterGain.gain.value = 1;
      state.masterGain.connect(state.audioContext.destination);
    }

    return state.audioContext;
  }

  function getMasterGain() {
    getAudioContext();
    return state.masterGain;
  }

  async function ensureContextRunning() {
    const ctx = getAudioContext();
    if (ctx.state === "suspended") {
      await ctx.resume();
    }
    return ctx;
  }

  function bindGlobalEvents() {
    els.bpmGlobal.addEventListener("input", () => {
      els.bpmValue.textContent = `${els.bpmGlobal.value} BPM`;
      updateActivePlaybackParams();
    });

    els.keyGlobal.addEventListener("change", () => {
      updateActivePlaybackParams();
    });

    els.playSession.addEventListener("click", playSession);
    els.pauseSession.addEventListener("click", pauseSession);
    els.stopSession.addEventListener("click", stopSession);
    els.renderMix.addEventListener("click", renderMixdown);

    window.addEventListener("beforeunload", () => {
      if (state.audioContext) {
        state.audioContext.close().catch(() => {});
      }
    });
  }

  function bindTrackEvents(track) {
    track.input.addEventListener("change", (event) => {
      const [file] = event.target.files || [];
      if (file) {
        void handleFileSelection(track, file);
      }
    });

    track.dropzone.addEventListener("click", () => track.input.click());

    track.dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      track.dropzone.classList.add("is-dragover");
    });

    track.dropzone.addEventListener("dragleave", () => {
      track.dropzone.classList.remove("is-dragover");
    });

    track.dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      track.dropzone.classList.remove("is-dragover");
      const [file] = event.dataTransfer.files || [];
      if (file) {
        void handleFileSelection(track, file);
      }
    });

    track.volume.addEventListener("input", () => {
      track.userVolume = Number(track.volume.value);
      track.volumeValue.textContent = `${Math.round(track.userVolume * 100)}%`;
      updateTrackGain(track);
    });

    track.muteButton.addEventListener("click", () => {
      track.mute = !track.mute;
      refreshTrackUi(track);
      updateAllTrackGains();
    });

    track.soloButton.addEventListener("click", () => {
      track.solo = !track.solo;
      refreshTrackUi(track);
      updateAllTrackGains();
    });

    track.clearButton.addEventListener("click", () => {
      clearTrack(track);
    });
  }

  function createWaveBars(track) {
    const pattern = [22, 58, 38, 74, 31, 68, 45, 82, 29, 61, 36, 70];
    track.waveform.innerHTML = "";
    track.bars = pattern.map((height, index) => {
      const bar = document.createElement("div");
      bar.className = "wave-bar";
      bar.style.height = `${height}%`;
      bar.style.backgroundColor = track.color;
      bar.style.animationDelay = `${index * 70}ms`;
      track.waveform.appendChild(bar);
      return bar;
    });
  }

  async function handleFileSelection(track, file) {
    if (!isAcceptedFile(file.name)) {
      setStatus("Apenas arquivos .mp3 ou .wav são aceitos", "error");
      return;
    }

    await ensureContextRunning();
    track.file = file;
    track.fileLabel.textContent = file.name;
    track.processing = true;
    track.trackState.textContent = "PROCESSING IN PYTHON BACKEND...";
    track.panel.classList.add("has-audio", "is-processing");
    setStatus(`Processando ${file.name} no backend Python...`, "loading");

    if (track.requestController) {
      track.requestController.abort();
    }

    const controller = new AbortController();
    track.requestController = controller;

    try {
      const processedBuffer = await processTrackThroughBackend(track, file, controller.signal);
      if (track.requestController !== controller) {
        return;
      }

      track.buffer = processedBuffer;
      track.processedBpm = Number(els.bpmGlobal.value);
      track.processedKeyId = noteToSemitone(els.keyGlobal.value);
      track.trackState.textContent = "Loaded";

      if (state.isPlaying) {
        restartSessionSilently();
      }

      refreshTrackUi(track);
      updateSessionUi();
      setStatus(`Stem sincronizado: ${file.name}`, "ready");
    } catch (error) {
      if (error.name === "AbortError") {
        return;
      }

      if (track.requestController !== controller) {
        return;
      }

      console.error(error);
      track.buffer = null;
      track.trackState.textContent = "Backend error";
      track.panel.classList.remove("has-audio");
      refreshTrackUi(track);
      updateSessionUi();
      setStatus(`Falha ao processar ${file.name}`, "error");
    } finally {
      if (track.requestController === controller) {
        track.processing = false;
        track.requestController = null;
        track.panel.classList.remove("is-processing");
        track.input.value = "";
        refreshTrackUi(track);
      }
    }
  }

  async function processTrackThroughBackend(track, file, signal) {
    const metadata = TRACK_SIMULATED_METADATA[track.id] || TRACK_SIMULATED_METADATA.drums;
    const formData = new FormData();
    formData.append("instrumento", track.id);
    formData.append("original_bpm", String(metadata.originalBpm));
    formData.append("original_key_id", String(metadata.originalKeyId));
    formData.append("target_bpm", String(Number(els.bpmGlobal.value)));
    formData.append("target_key_id", String(noteToSemitone(els.keyGlobal.value)));
    formData.append("arquivo", file, file.name);

    const response = await fetch(BACKEND_URL, {
      method: "POST",
      body: formData,
      cache: "no-store",
      signal,
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `Backend retornou status ${response.status}`);
    }

    const audioBlob = await response.blob();
    const audioArrayBuffer = await audioBlob.arrayBuffer();
    return getAudioContext().decodeAudioData(audioArrayBuffer.slice(0));
  }

  function isAcceptedFile(fileName) {
    const ext = fileName.split(".").pop()?.toLowerCase();
    return ACCEPTED_EXTENSIONS.includes(ext || "");
  }

  function refreshTrackUi(track) {
    const percent = Math.round(track.userVolume * 100);
    track.volumeValue.textContent = `${percent}%`;
    track.muteButton.classList.toggle("border-white/10", !track.mute);
    track.muteButton.classList.toggle("border-rose-400/50", track.mute);
    track.muteButton.classList.toggle("text-rose-200", track.mute);
    track.soloButton.classList.toggle("border-white/10", !track.solo);
    track.soloButton.classList.toggle("border-cyan-400/40", track.solo);
    track.soloButton.classList.toggle("text-cyan-200", track.solo);
    updateTrackGain(track);
    updateWaveState(track);
  }

  function updateTrackGain(track) {
    const anySolo = Array.from(state.tracks.values()).some((item) => item.solo);
    const shouldMute = track.mute || (anySolo && !track.solo) || !track.buffer;
    track.gainNode.gain.value = shouldMute ? 0 : track.userVolume;
  }

  function updateAllTrackGains() {
    state.tracks.forEach((track) => updateTrackGain(track));
  }

  function clearTrack(track) {
    if (track.requestController) {
      track.requestController.abort();
    }

    if (track.source) {
      try {
        track.source.stop();
      } catch {
        // Fonte já pode ter sido parada.
      }
      try {
        track.source.disconnect();
      } catch {
        // Ignora erro de desconexão.
      }
      track.source = null;
    }

    track.file = null;
    track.buffer = null;
    track.processing = false;
    track.requestController = null;
    track.processedBpm = null;
    track.processedKeyId = null;
    track.panel.classList.remove("has-audio", "is-processing", "is-playing");
    track.fileLabel.textContent = "Nenhum arquivo selecionado";
    track.trackState.textContent = "Idle";
    track.input.value = "";

    refreshTrackUi(track);
    updateAllTrackGains();
    updateSessionUi();

    if (state.isPlaying) {
      restartSessionSilently();
    }

    setStatus(`${track.label} limpa com sucesso`, "ready");
  }

  async function playSession() {
    if (state.renderBusy) {
      return;
    }

    const loadedTracks = Array.from(state.tracks.values()).filter((track) => track.buffer);
    if (loadedTracks.length === 0) {
      setStatus("Carregue ao menos um stem antes de tocar", "error");
      return;
    }

    await ensureContextRunning();

    const hasPausedSources = Array.from(state.tracks.values()).some((track) => track.source);
    if (state.isPlaying) {
      return;
    }

    if (hasPausedSources && state.audioContext.state === "running") {
      state.isPlaying = true;
      updateActivePlaybackParams();
      updateSessionUi();
      setStatus("Sessão retomada", "playing");
      state.tracks.forEach((track) => updateWaveState(track, true));
      return;
    }

    stopTrackSources();
    scheduleTrackSources();
    state.isPlaying = true;
    state.sessionStartTime = getAudioContext().currentTime;
    updateSessionUi();
    setStatus("Sessão em reprodução", "playing");
  }

  function pauseSession() {
    if (!state.audioContext) {
      return;
    }

    if (!state.isPlaying) {
      setStatus("A sessão já está pausada", "ready");
      return;
    }

    state.audioContext.suspend();
    state.isPlaying = false;
    updateSessionUi();
    setStatus("Sessão pausada", "paused");
  }

  function stopSession() {
    stopTrackSources();
    state.isPlaying = false;
    if (state.audioContext && state.audioContext.state !== "closed") {
      state.audioContext.suspend().catch(() => {});
    }
    updateSessionUi();
    setStatus("Sessão parada", "ready");
  }

  function stopTrackSources() {
    state.tracks.forEach((track) => {
      if (track.source) {
        try {
          track.source.stop();
        } catch {
          // Ignora o erro caso a fonte já tenha sido parada.
        }
        track.source.disconnect();
        track.source = null;
      }
    });
  }

  function scheduleTrackSources() {
    const ctx = getAudioContext();
    const startAt = ctx.currentTime + 0.15;

    state.tracks.forEach((track) => {
      if (!track.buffer) {
        updateWaveState(track);
        return;
      }

      const source = ctx.createBufferSource();
      source.buffer = track.buffer;
      source.loop = true;
      source.connect(track.gainNode);
      source.start(startAt);
      track.source = source;
    });

    updateActivePlaybackParams();
    updateAllTrackGains();
    state.tracks.forEach((track) => updateWaveState(track, true));
  }

  function updateActivePlaybackParams() {
    state.tracks.forEach((track) => {
      if (!track.source) {
        return;
      }

      const currentBpm = Number(els.bpmGlobal.value);
      const currentKeyId = noteToSemitone(els.keyGlobal.value);
      const bpmRatio = track.processedBpm ? currentBpm / track.processedBpm : 1;
      const keyOffset = track.processedKeyId !== null && track.processedKeyId !== undefined
        ? currentKeyId - track.processedKeyId
        : 0;

      track.source.playbackRate.value = bpmRatio;
      track.source.detune.value = keyOffset * 100;
    });
  }

  function restartSessionSilently() {
    if (!state.isPlaying) {
      return;
    }

    stopTrackSources();
    scheduleTrackSources();
    setStatus("Sessão sincronizada com novo stem", "playing");
  }

  async function renderMixdown() {
    if (state.renderBusy) {
      return;
    }

    const loadedTracks = Array.from(state.tracks.values()).filter((track) => track.buffer);
    if (loadedTracks.length === 0) {
      setStatus("Carregue stems antes de renderizar", "error");
      return;
    }

    state.renderBusy = true;
    els.renderMix.disabled = true;
    els.renderMix.classList.add("opacity-60", "cursor-not-allowed");
    setStatus("Renderizando remix em WAV...", "loading");

    try {
      const renderDuration = Math.max(
        DEFAULT_RENDER_SECONDS,
        ...loadedTracks.map((track) => track.buffer.duration)
      );
      const offline = new OfflineAudioContext(2, Math.ceil(renderDuration * SAMPLE_RATE), SAMPLE_RATE);
      const master = offline.createGain();
      master.gain.value = 1;
      master.connect(offline.destination);

      const anySolo = loadedTracks.some((track) => track.solo);

      loadedTracks.forEach((track) => {
        const source = offline.createBufferSource();
        const gain = offline.createGain();
        const effectiveVolume = track.mute || (anySolo && !track.solo) ? 0 : track.userVolume;
        const currentBpm = Number(els.bpmGlobal.value);
        const currentKeyId = noteToSemitone(els.keyGlobal.value);
        const bpmRatio = track.processedBpm ? currentBpm / track.processedBpm : 1;
        const keyOffset = track.processedKeyId !== null && track.processedKeyId !== undefined
          ? currentKeyId - track.processedKeyId
          : 0;

        source.buffer = track.buffer;
        source.loop = true;
        source.playbackRate.value = bpmRatio;
        source.detune.value = keyOffset * 100;
        gain.gain.value = effectiveVolume;
        source.connect(gain).connect(master);
        source.start(0);
      });

      const renderedBuffer = await offline.startRendering();
      const wavBlob = audioBufferToWav(renderedBuffer);
      triggerDownload(wavBlob, buildFileName());
      setStatus("Remix renderizado e baixado com sucesso", "ready");
    } catch (error) {
      console.error(error);
      setStatus("Falha ao renderizar o remix", "error");
    } finally {
      state.renderBusy = false;
      els.renderMix.disabled = false;
      els.renderMix.classList.remove("opacity-60", "cursor-not-allowed");
    }
  }

  function audioBufferToWav(buffer) {
    const numberOfChannels = buffer.numberOfChannels;
    const sampleRate = buffer.sampleRate;
    const length = buffer.length * numberOfChannels * 2 + 44;
    const arrayBuffer = new ArrayBuffer(length);
    const view = new DataView(arrayBuffer);

    writeString(view, 0, "RIFF");
    view.setUint32(4, 36 + buffer.length * numberOfChannels * 2, true);
    writeString(view, 8, "WAVE");
    writeString(view, 12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numberOfChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * numberOfChannels * 2, true);
    view.setUint16(32, numberOfChannels * 2, true);
    view.setUint16(34, 16, true);
    writeString(view, 36, "data");
    view.setUint32(40, buffer.length * numberOfChannels * 2, true);

    const channelData = [];
    for (let channel = 0; channel < numberOfChannels; channel += 1) {
      channelData.push(buffer.getChannelData(channel));
    }

    let offset = 44;
    for (let sample = 0; sample < buffer.length; sample += 1) {
      for (let channel = 0; channel < numberOfChannels; channel += 1) {
        const sampleValue = Math.max(-1, Math.min(1, channelData[channel][sample] || 0));
        view.setInt16(offset, sampleValue < 0 ? sampleValue * 0x8000 : sampleValue * 0x7fff, true);
        offset += 2;
      }
    }

    return new Blob([arrayBuffer], { type: "audio/wav" });
  }

  function writeString(view, offset, text) {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  }

  function triggerDownload(blob, fileName) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = fileName;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function buildFileName() {
    const bpm = els.bpmGlobal.value;
    const key = els.keyGlobal.value.replace(/[^a-zA-Z0-9]+/g, "-");
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    return `maquina-de-rock-remix-${key}-${bpm}bpm-${stamp}.wav`;
  }

  function updateWaveState(track, forcePlaying = false) {
    const active = (state.isPlaying || forcePlaying) && Boolean(track.buffer) && !track.mute;
    track.panel.classList.toggle("is-playing", active);
    track.panel.classList.toggle("is-processing", track.processing);

    if (track.processing) {
      track.trackState.textContent = "PROCESSING IN PYTHON BACKEND...";
      return;
    }

    track.trackState.textContent = track.buffer ? (active ? "Playing" : "Loaded") : "Idle";
  }

  function updateSessionUi() {
    const anyLoaded = Array.from(state.tracks.values()).some((track) => track.buffer);
    const anySolo = Array.from(state.tracks.values()).some((track) => track.solo);

    if (state.isPlaying) {
      els.sessionLed.className = "h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-[0_0_16px_rgba(16,185,129,0.7)]";
      els.sessionStatus.textContent = anySolo ? "Sessão tocando com solo ativo" : "Sessão tocando em sync";
      els.footerSession.textContent = "Mixagem em reprodução ao vivo";
      return;
    }

    if (anyLoaded) {
      els.sessionLed.className = "h-2.5 w-2.5 rounded-full bg-cyan-400 shadow-[0_0_16px_rgba(6,182,212,0.5)]";
      els.sessionStatus.textContent = "Stems carregados e prontos";
      els.footerSession.textContent = "Arquivos carregados, aguardando play";
      return;
    }

    els.sessionLed.className = "h-2.5 w-2.5 rounded-full bg-slate-500";
    els.sessionStatus.textContent = "Pronto para carregar stems";
    els.footerSession.textContent = "Nenhum áudio reproduzindo";
  }

  function setStatus(message, tone) {
    els.sessionStatus.textContent = message;
    els.footerSession.textContent = message;

    const toneMap = {
      ready: "bg-cyan-400 shadow-[0_0_16px_rgba(6,182,212,0.5)]",
      loading: "bg-amber-400 shadow-[0_0_16px_rgba(245,158,11,0.5)]",
      playing: "bg-emerald-400 shadow-[0_0_16px_rgba(16,185,129,0.7)]",
      paused: "bg-rose-400 shadow-[0_0_16px_rgba(244,63,94,0.55)]",
      error: "bg-rose-500 shadow-[0_0_16px_rgba(244,63,94,0.75)]",
    };

    els.sessionLed.className = `h-2.5 w-2.5 rounded-full ${toneMap[tone] || toneMap.ready}`;
  }

  function noteToSemitone(note) {
    return NOTE_TO_SEMITONE[note] ?? 0;
  }

  function getPitchOffsetSemitones(sourceKey, targetKey) {
    const source = noteToSemitone(sourceKey);
    const target = noteToSemitone(targetKey);
    const delta = target - source;
    return delta > 6 ? delta - 12 : delta < -6 ? delta + 12 : delta;
  }

  window.MaquinaDeRockStudio = {
    get state() {
      return {
        isPlaying: state.isPlaying,
        bpm: Number(els.bpmGlobal.value),
        key: els.keyGlobal.value,
        loadedTracks: Array.from(state.tracks.values()).map((track) => ({
          id: track.id,
          hasAudio: Boolean(track.buffer),
          fileName: track.file?.name || null,
        })),
      };
    },
    getPitchOffsetSemitones,
  };

  document.addEventListener("DOMContentLoaded", init);
})();
