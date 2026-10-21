/**
 * Pitchlight — shared script view: draws a script (sections of spoken lines) with the presenter's place in it.
 * Used by Meeting Mode's Script tab and the practice page. `sections` come from the server so line numbers match.
 */
(function () {
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /** status: { next, covered: [idx], skipped: [sectionIdx], pct }  (null before anything is said) */
  function render(sections, status, opts = {}) {
    if (!sections || !sections.length) return '';
    const covered = new Set((status && status.covered) || []);
    const skipped = new Set((status && status.skipped) || []);
    const next = status && Number.isInteger(status.next) ? status.next : (status ? -1 : 0);
    let idx = 0;
    return sections.map((sec, si) => {
      const lines = sec.lines.map((text) => {
        const i = idx++;
        const cls = i === next ? 'is-next' : covered.has(i) ? 'is-done' : '';
        return `<li class="sv-line ${cls}" data-idx="${i}"${opts.clickable ? ' tabindex="0" role="button"' : ''}>${esc(text)}</li>`;
      }).join('');
      return `<div class="sv-sec${skipped.has(si) ? ' is-skipped' : ''}">
        <div class="sv-title">${esc(sec.title)}${skipped.has(si) ? ' <em>skipped</em>' : ''}</div>
        <ol class="sv-lines" start="1">${lines}</ol></div>`;
    }).join('');
  }

  /** Keep the "next" line in view without moving the whole page. */
  function scrollToNext(container) {
    const el = container && container.querySelector('.sv-line.is-next');
    if (!el) return;
    const top = el.offsetTop - container.offsetTop - container.clientHeight / 3;
    container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }

  window.ScriptView = { render, scrollToNext, esc };
})();
