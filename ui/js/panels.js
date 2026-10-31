/**
 * panels.js — Side-panel controllers
 *
 * Manages:
 *   - Panel: Settings (opens Dialogs.openSettings)
 *
 */

// ── Settings Panel ────────────────────────────────────────────────────────────
const SettingsPanel = (() => {
  function init() {
    const btn = document.getElementById('open-settings-dialog');
    if (btn) btn.addEventListener('click', () => Dialogs.openSettings());
  }
  return { init };
})();


// ── Boot all panels ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  SettingsPanel.init();
});
