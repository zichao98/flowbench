/* Flowbench icon set: one stroke weight, one grid (24px), rendered as inline SVG. */
(function () {
  'use strict';
  const P = {
    play: '<path d="M8 5.5v13l10-6.5z" fill="currentColor" stroke="none"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" stroke="none"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="M7.5 10l3 2.5-3 2.5M12.5 15.5h4"/>',
    undo: '<path d="M9 14.5L4.5 10 9 5.5"/><path d="M4.5 10h9.5a5.5 5.5 0 0 1 0 11H11"/>',
    redo: '<path d="M15 14.5l4.5-4.5L15 5.5"/><path d="M19.5 10H10a5.5 5.5 0 0 0 0 11h3"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2.2h7.2a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    'folder-open': '<path d="M3.5 17.5V7.5a2 2 0 0 1 2-2h3.8l2 2.2h6.2a2 2 0 0 1 2 2v1"/><path d="M3.5 17.5l2.3-5.2a1.5 1.5 0 0 1 1.4-.9h12.1a1 1 0 0 1 .9 1.4l-2.1 4.8a1.5 1.5 0 0 1-1.4.9H5a1.5 1.5 0 0 1-1.5-1z"/>',
    save: '<path d="M12 4v10.5"/><path d="M7.5 10.5L12 15l4.5-4.5"/><path d="M5 19.5h14"/>',
    'file-code': '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/><path d="M14 3.5V8h4.5"/><path d="M10.5 12.5l-2 2 2 2M13.5 12.5l2 2-2 2"/>',
    file: '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/><path d="M14 3.5V8h4.5"/>',
    theme: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none"/>',
    refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4.5H15"/>',
    'chevron-left': '<path d="M14.5 6l-6 6 6 6"/>',
    'chevron-right': '<path d="M9.5 6l6 6-6 6"/>',
    'chevron-down': '<path d="M6 9.5l6 6 6-6"/>',
    func: '<path d="M15.5 4.5H14a3 3 0 0 0-3 3v12"/><path d="M8 11h7"/>',
    code: '<path d="M9 7.5L4.5 12 9 16.5M15 7.5l4.5 4.5-4.5 4.5"/>',
    close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    fit: '<path d="M4.5 9V5.5a1 1 0 0 1 1-1H9M15 4.5h3.5a1 1 0 0 1 1 1V9M19.5 15v3.5a1 1 0 0 1-1 1H15M9 19.5H5.5a1 1 0 0 1-1-1V15"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>',
    expand: '<path d="M14 4.5h5.5V10M10 19.5H4.5V14M19.5 4.5l-6 6M4.5 19.5l6-6"/>',
    layout: '<rect x="3.5" y="4" width="7" height="6" rx="1.5"/><rect x="13.5" y="14" width="7" height="6" rx="1.5"/><path d="M10.5 7h3a2 2 0 0 1 2 2v5"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h1M10 10h1M13.5 10h1M17 10h.5M7.5 14h9"/>',
    box: '<path d="M12 3.5l7.5 4.2v8.6L12 20.5l-7.5-4.2V7.7z"/><path d="M4.5 7.7L12 12l7.5-4.3M12 12v8.5"/>'
  };
  window.ICON = (name, cls) => `<svg class="ico${cls ? ' ' + cls : ''}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || ''}</svg>`;
  document.querySelectorAll('[data-icon]').forEach(el => el.insertAdjacentHTML('afterbegin', window.ICON(el.dataset.icon)));
})();
