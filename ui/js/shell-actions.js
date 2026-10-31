/**
 * shell-actions.js — files handed over from the File Explorer right-click menu.
 *
 * Office Axe --open | --convert-pdf | --compress | --merge "<path>" (see
 * shell_menu.py / shell_ipc.py). Python groups the paths per action and sends
 *   [{action: 'open' | 'convert' | 'compress' | 'merge', paths: [...]}]
 * first through shell_take_pending() (once the app is unlocked), then as the
 * 'shell_actions' event. Each action reuses the normal tools, so conversions
 * show the usual progress card and originals are always kept.
 */
const ShellActions = (() => {
  const dirOf = p => String(p || '').replace(/[\\/][^\\/]*$/, '');

  async function showFolderOf(path, select = true) {
    const dir = dirOf(path);
    if (!dir) return;
    await FileTree.loadFolder(dir);
    if (select && FileTree.revealPath) await FileTree.revealPath(path);
  }

  async function run(group) {
    const paths = (group.paths || []).filter(Boolean);
    if (!paths.length) return;
    App.switchPanel('workspace');
    switch (group.action) {
      case 'open': {
        const p = paths[0];
        const info = await SFM.call('path_exists', p).catch(() => null);
        if (info && info.is_dir) await FileTree.loadFolder(p);
        else await showFolderOf(p);
        break;
      }
      case 'convert': {
        await showFolderOf(paths[0], false);
        const docs = paths.filter(p => ConvertTools.isDoc(p));
        const imgs = paths.filter(p => ConvertTools.isImage(p));
        if (docs.length) await ConvertTools.docsToPdf(docs);
        if (imgs.length === 1) await ConvertTools.quickImageToPdf(imgs[0]);
        else if (imgs.length > 1) ConvertTools.openImagesToPdf(imgs);
        if (!docs.length && !imgs.length) App.toast('These files can’t be converted to PDF', 'warning');
        break;
      }
      case 'compress':
        await showFolderOf(paths[0]);
        ConvertTools.openCompress(paths);
        break;
      case 'merge':
        await showFolderOf(paths[0], false);
        PdfTools.openMerge(paths, dirOf(paths[0]));
        break;
    }
  }

  async function runAll(groups) {
    for (const g of groups || []) {
      try { await run(g); } catch (e) { console.error('[ShellActions]', g, e); }
    }
  }

  // Take what arrived before the page was ready — but only once the app is
  // unlocked (the subscription screen reloads the page when it closes).
  let taken = false;
  async function poll() {
    if (taken) return;
    const locked = document.body.classList.contains('sub-locked') ||
                   !!document.querySelector('#sub-gate.is-open');
    if (!locked && typeof FileTree !== 'undefined') {
      const r = await SFM.call('shell_take_pending').catch(() => null);
      if (r && r.ok) {
        taken = true;
        if (r.actions && r.actions.length) runAll(r.actions);
        return;
      }
    }
    setTimeout(poll, 600);
  }

  function init() {
    SFM.on('shell_actions', runAll);
    setTimeout(poll, 1200);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return { run, runAll };
})();
