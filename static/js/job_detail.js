// Job detail: polls the job JSON endpoint and animates phase/progress state.
(function () {
  const statusChip = document.getElementById('statusChip');
  const phaseTitle = document.getElementById('phaseTitle');
  const progressEl = document.getElementById('progress');
  const barEl = document.getElementById('bar');
  const totalElapsedEl = document.getElementById('totalElapsed');
  const cancelForm = document.getElementById('cancelJobForm');
  const cancelButton = document.getElementById('cancelJobButton');
  const cancellingChip = document.getElementById('cancellingChip');
  const phaseCards = new Map(Array.from(document.querySelectorAll('.phase-card')).map(card => [card.dataset.phase, card]));
  const PHASE_ORDER = ['top_n', 'collection', 'details', 'cleanup'];
  let pollingActive = true;

  if (cancelForm && cancelButton) {
    cancelForm.addEventListener('submit', () => {
      cancelButton.disabled = true;
      cancelButton.textContent = 'Cancelling...';
    });
  }

  function toggleCancelControls(status) {
    const normalized = (status || '').toLowerCase();
    const isCancellable = normalized === 'pending' || normalized === 'running';
    if (cancelForm) {
      cancelForm.style.display = isCancellable ? 'inline-flex' : 'none';
    }
    if (cancelButton) {
      cancelButton.disabled = normalized === 'cancelling';
    }
    if (cancellingChip) {
      cancellingChip.style.display = normalized === 'cancelling' ? 'inline-flex' : 'none';
    }
  }

  toggleCancelControls(statusChip ? statusChip.textContent : '');

  function formatDuration(ms) {
    if (!Number.isFinite(ms) || ms <= 0) return '0s';
    const totalSeconds = Math.floor(ms / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const parts = [];
    if (hours) parts.push(hours + 'h');
    if (minutes || hours) parts.push(minutes + 'm');
    parts.push(seconds + 's');
    return parts.join(' ');
  }

  function refreshTimes() {
    document.querySelectorAll('.localtime').forEach(el => {
      const ts = el.getAttribute('data-ts');
      if (!ts) return;
      try {
        const d = new Date(ts);
        el.textContent = d.toLocaleString();
      } catch (e) { }
    });
  }

  function toTitle(value) {
    if (!value) return 'Pending';
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  function getCurrentPhase(phases) {
    if (!phases) return null;
    for (const key of PHASE_ORDER) {
      if ((phases[key] || {}).status === 'running') return key;
    }
    for (const key of PHASE_ORDER.slice().reverse()) {
      if ((phases[key] || {}).status === 'done') return key;
    }
    return null;
  }

  function updateTotalElapsed(nowMs) {
    if (!totalElapsedEl) return;
    const createdTs = totalElapsedEl.dataset.created;
    if (!createdTs) {
      totalElapsedEl.textContent = '--';
      return;
    }
    const start = new Date(createdTs).getTime();
    const finishAttr = totalElapsedEl.dataset.finished;
    // If finished, use finish time. If running, use now.
    // If we stopped polling because of error/cancel, we rely on finishAttr being set.
    if (finishAttr) {
      const end = new Date(finishAttr).getTime();
      totalElapsedEl.textContent = formatDuration(Math.max(0, end - start));
      return;
    }
    // If no finish attribute but polling stopped (e.g. cancelled/error without finish time?), stop counting?
    // Ideally backend sets finished_at. If not, we might count indefinitely unless we check status.
    // But we have no easy status check here unless we store it globally.
    // Let's rely on finishAttr.

    const end = nowMs;
    if (!Number.isFinite(start)) {
      totalElapsedEl.textContent = '--';
      return;
    }
    totalElapsedEl.textContent = formatDuration(Math.max(0, end - start));
  }

  function updateDurations() {
    // If polling stopped and we have a finish time, we don't strictly need to keep updating,
    // but updating allows us to catch the final set.
    // However, if we are 'done' or 'error' but finished_at is missing (bug), we don't want to count forever.
    // Let's rely on the fact that we set data-finished when status is final.

    const nowMs = Date.now();
    updateTotalElapsed(nowMs);
    phaseCards.forEach(card => {
      const startAttr = card.dataset.start;
      const finishAttr = card.dataset.finish;
      const start = startAttr ? new Date(startAttr).getTime() : null;
      const finish = finishAttr ? new Date(finishAttr).getTime() : null;
      const elapsedEl = card.querySelector('[data-role="elapsed"]');
      if (elapsedEl) {
        if (start) {
          const end = finish || nowMs;
          elapsedEl.textContent = formatDuration(Math.max(0, end - start));
        } else {
          elapsedEl.textContent = '--';
        }
      }
      // ... (ETA logic unchanged, omitted for brevity if not changing) ...
      const progress = Number(card.dataset.progress || '0');
      const total = Number(card.dataset.total || '0');
      const etaEl = card.querySelector('[data-role="eta"]');
      if (etaEl) {
        if (finish) {
          etaEl.textContent = 'Complete';
        } else if (start && progress > 0 && total > 0 && total >= progress) {
          const elapsedMs = nowMs - start;
          const projected = elapsedMs * (total / progress);
          const remaining = Math.max(0, projected - elapsedMs);
          etaEl.textContent = formatDuration(remaining);
        } else {
          etaEl.textContent = '--';
        }
      }
    });
  }

  function updatePhaseCards(phases) {
    const active = getCurrentPhase(phases);
    phaseCards.forEach((card, key) => {
      const data = (phases && phases[key]) || {};
      const statusText = toTitle(data.status || card.dataset.defaultStatus || 'pending');
      const statusEl = card.querySelector('[data-role="status"]');
      if (statusEl) statusEl.textContent = statusText;
      const progressVal = 'progress' in data ? data.progress || 0 : Number(card.dataset.progress || 0);
      const totalVal = 'total' in data ? data.total || 0 : Number(card.dataset.total || 0);
      const progressDisplay = totalVal ? progressVal + ' / ' + totalVal : String(progressVal);
      const progressEl = card.querySelector('[data-role="progress"]');
      if (progressEl) progressEl.textContent = progressDisplay;
      if (data.started_at) {
        card.dataset.start = data.started_at;
      } else {
        delete card.dataset.start;
      }
      if (data.finished_at) {
        card.dataset.finish = data.finished_at;
      } else {
        delete card.dataset.finish;
      }
      card.dataset.progress = progressVal;
      card.dataset.total = totalVal;
      const extraEl = card.querySelector('[data-role="extra"]');
      if (extraEl) {
        let extra = '';
        if (key === 'collection') {
          const users = data.users_completed ?? data.progress;
          const totalUsers = data.total ?? 0;
          const items = data.items ?? null;
          const parts = [];
          if (totalUsers) parts.push((users || 0) + '/' + totalUsers + ' users');
          if (items) parts.push(items + ' items');
          extra = parts.join(' - ');
        } else if (key === 'cleanup') {
          extra = 'Pruning removed games';
        } else if (data.batch) {
          extra = 'Batch size: ' + data.batch;
        }
        extraEl.textContent = extra || ' ';
      }
      card.classList.toggle('is-active', active === key);
    });
    updateDurations();
    return active;
  }

  function updateOverallProgress(progress, total) {
    if (progressEl) progressEl.textContent = progress + ' / ' + total;
    if (barEl) {
      const pct = total ? Math.min(100, Math.round((progress / total) * 100)) : 0;
      barEl.style.width = pct + '%';
    }
  }

  refreshTimes();
  updateDurations();
  const timerInterval = setInterval(updateDurations, 1000);

  function poll() {
    fetch(window.location.href, { headers: { 'x-requested-with': 'XMLHttpRequest' } })
      .then(resp => (resp.ok ? resp.json() : null))
      .then(data => {
        if (!data) return;
        const status = data.status || '';
        if (statusChip) statusChip.textContent = toTitle(status);
        toggleCancelControls(status);
        updateOverallProgress(data.progress || 0, data.total || 0);

        const activePhase = updatePhaseCards(data.phases || {});
        if (phaseTitle) phaseTitle.textContent = 'Active phase: ' + (activePhase ? toTitle(activePhase.replace('_', ' ')) : '--');

        if (data.created_at && totalElapsedEl) totalElapsedEl.dataset.created = data.created_at;
        if (totalElapsedEl) {
          if (data.finished_at) {
            totalElapsedEl.dataset.finished = data.finished_at;
          } else {
            delete totalElapsedEl.dataset.finished;
          }
        }

        const done = status === 'done' || status === 'error' || status === 'cancelled';
        if (done) {
          pollingActive = false;
          updateDurations(); // Perform one final update to ensure 'finished_at' is used
          clearInterval(timerInterval); // Stop the loop so it doesn't flick back if something is weird, but mainly to save CPU
          toggleCancelControls('');
        } else {
          setTimeout(poll, 1500);
        }
      })
      .catch(() => {
        setTimeout(poll, 4000);
      });
  }

  setTimeout(poll, 1500);
})();
