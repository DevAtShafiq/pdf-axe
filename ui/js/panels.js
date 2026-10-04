/**
 * panels.js — Side-panel controllers
 *
 * Manages:
 *   - Panel: Folder Maker (create student folders)
 *   - Panel: Watch Folder (queue, entries, status)
 *   - Panel: Apostille (links to dialog)
 *   - Panel: Heatmap (expiry heat map render)
 *   - Panel: Settings (opens Dialogs.openSettings)
 */

// ── Folder Maker Panel ────────────────────────────────────────────────────────
const FolderMakerPanel = (() => {
  function init() {
    const btn = document.getElementById('fm-create-btn');
    if (btn) btn.addEventListener('click', _run);

    const browse = document.getElementById('fm-dest-browse');
    if (browse) browse.addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r.ok && r.path) {
        const inp = document.getElementById('fm-dest');
        if (inp) inp.value = r.path;
      }
    });
  }

  async function _run() {
    const textarea = document.getElementById('fm-names');
    const destEl   = document.getElementById('fm-dest');
    if (!textarea || !destEl) return;

    const raw  = textarea.value.trim();
    const dest = destEl.value.trim();

    if (!raw)  { App.toast('Enter at least one name', 'error'); return; }
    if (!dest) { App.toast('Choose a destination folder', 'error'); return; }

    const names = raw.split('\n').map(s => s.trim()).filter(Boolean);

    // Safety gate: >10 folders → confirm
    if (names.length > 10) {
      if (!confirm(`Create ${names.length} folders in:\n${dest}\n\nContinue?`)) return;
    }

    App.setStatus(`Creating ${names.length} folders…`, true);
    const r = await SFM.createStudentFolders(names, dest);
    App.setStatus('Ready');

    if (r.ok) {
      App.toast(`Created ${r.created || names.length} folders`, 'success');
      const log = document.getElementById('fm-log');
      if (log) log.textContent = (r.log || '').trim() || `Done — ${names.length} folders created.`;
      if (r.dest) FileTree.navigate(r.dest);
    } else {
      App.toast('Failed: ' + r.error, 'error');
    }
  }

  return { init };
})();


// ── Watch Folder Panel ────────────────────────────────────────────────────────
const WatchPanel = (() => {
  let _pollTimer = null;

  function init() {
    const startBtn = document.getElementById('watch-start-btn');
    const stopBtn  = document.getElementById('watch-stop-btn');
    const browse   = document.getElementById('watch-folder-browse');
    const clearBtn = document.getElementById('watch-clear-btn');

    if (startBtn) startBtn.addEventListener('click', _start);
    if (stopBtn)  stopBtn.addEventListener('click', _stop);
    if (clearBtn) clearBtn.addEventListener('click', async () => {
      await SFM.watchClearDone();
      _refresh();
    });
    if (browse) browse.addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r.ok && r.path) {
        const inp = document.getElementById('watch-folder');
        if (inp) inp.value = r.path;
      }
    });
  }

  async function _start() {
    const folder = document.getElementById('watch-folder')?.value.trim();
    if (!folder) { App.toast('Choose a folder to watch', 'error'); return; }
    const r = await SFM.watchStart(folder);
    if (r.ok) {
      App.toast('Watch started', 'success');
      _startPoll();
    } else {
      App.toast('Failed: ' + r.error, 'error');
    }
  }

  async function _stop() {
    await SFM.watchStop();
    App.toast('Watch stopped', 'info');
    _stopPoll();
    _refresh();
  }

  function _startPoll() {
    _stopPoll();
    _refresh();
    _pollTimer = setInterval(_refresh, 4000);
  }

  function _stopPoll() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  async function _refresh() {
    try {
      const r = await SFM.watchEntries();
      if (!r.ok) return;
      const list = document.getElementById('watch-list');
      if (!list) return;
      const entries = r.entries || [];
      if (entries.length === 0) {
        list.innerHTML = '<p class="text-muted" style="padding:8px;font-size:12px">No files processed yet.</p>';
        return;
      }
      list.innerHTML = entries.map(e => {
        const icon = e.status === 'done' ? '✅' : e.status === 'error' ? '❌' : '⏳';
        return `<div class="watch-entry ${e.status || ''}">
          <span>${icon}</span>
          <span class="watch-name" title="${_esc(e.path || '')}">${_esc((e.name || e.path || '').split(/[\\/]/).pop())}</span>
          <span class="watch-status text-muted">${_esc(e.status || 'pending')}</span>
        </div>`;
      }).join('');
    } catch(e) {}
  }

  function _esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  return { init, refresh: _refresh };
})();


// ── Heatmap Panel ─────────────────────────────────────────────────────────────
const HeatmapPanel = (() => {
  function init() {
    const scanBtn  = document.getElementById('hm-scan-btn');
    const browse   = document.getElementById('hm-folder-browse');

    if (scanBtn)  scanBtn.addEventListener('click', _scan);
    if (browse) browse.addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r.ok && r.path) {
        const inp = document.getElementById('hm-folder');
        if (inp) inp.value = r.path;
      }
    });
  }

  async function _scan() {
    const folder = document.getElementById('hm-folder')?.value.trim();
    if (!folder) { App.toast('Choose a folder to scan', 'error'); return; }
    App.setStatus('Scanning for expiry dates…', true);
    const r = await SFM.getExpiryHeatmap(folder);
    App.setStatus('Ready');
    if (!r.ok) { App.toast('Scan failed: ' + r.error, 'error'); return; }
    _render(r.items || []);
  }

  function _render(items) {
    const container = document.getElementById('hm-grid');
    if (!container) return;
    if (items.length === 0) {
      container.innerHTML = '<p class="text-muted" style="padding:8px;font-size:12px">No expiry dates found.</p>';
      return;
    }
    const now = Date.now();
    const sorted = [...items].sort((a, b) => (a.expiry_ts||0) - (b.expiry_ts||0));
    container.innerHTML = sorted.map(item => {
      const ts  = item.expiry_ts ? item.expiry_ts * 1000 : null;
      const days = ts ? Math.round((ts - now) / 86400000) : null;
      let cls = 'hm-ok';
      if (days !== null) {
        if (days < 0)   cls = 'hm-expired';
        else if (days <= 30)  cls = 'hm-danger';
        else if (days <= 90)  cls = 'hm-warn';
      }
      const label = days === null ? 'Unknown'
        : days < 0 ? `Expired ${Math.abs(days)}d ago`
        : days === 0 ? 'Expires today'
        : `${days}d left`;
      return `<div class="hm-card ${cls}" title="${_esc(item.path||'')}">
        <div class="hm-name">${_esc((item.name||'').split(/[\\/]/).pop())}</div>
        <div class="hm-expiry">${_esc(item.expiry_str || label)}</div>
        <div class="hm-days">${_esc(label)}</div>
      </div>`;
    }).join('');
  }

  function _esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  return { init };
})();


// ── Settings Panel ────────────────────────────────────────────────────────────
const SettingsPanel = (() => {
  function init() {
    const btn = document.getElementById('open-settings-dialog');
    if (btn) btn.addEventListener('click', () => Dialogs.openSettings());
  }
  return { init };
})();


// ── Apostille Panel ───────────────────────────────────────────────────────────
const ApostillePanel = (() => {
  function init() {
    const btn = document.getElementById('apostille-open-btn');
    if (btn) btn.addEventListener('click', () => {
      const folder = App.state.currentFolder || '';
      Dialogs.openApostille(folder);
    });
  }
  return { init };
})();


// ── Boot all panels ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  FolderMakerPanel.init();
  WatchPanel.init();
  HeatmapPanel.init();
  SettingsPanel.init();
  ApostillePanel.init();
});
