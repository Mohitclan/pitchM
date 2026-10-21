/**
 * Pitchlight shared header: logo, one navigation for every page, Help and the signed-in user chip.
 * A page only declares `<header class="pl-header" data-page="meeting"></header>`.
 * Optional `<div class="pl-header-extra">…</div>` inside the header (e.g. the practice app's timer) is kept before the user chip.
 */
(function () {
  'use strict';
  const ITEMS = [
    { id: 'home', label: 'Home', href: '/home' },
    { id: 'meeting', label: 'Meeting', href: '/meeting' },
    { id: 'practice', label: 'Practice', href: '/app' },
    { id: 'library', label: 'Library', children: [
      { id: 'prepare', label: 'Prepare me', sub: 'Draft a script and answers for a call', href: '/prepare' },
      { id: 'company', label: 'Company hub', sub: 'Shared product knowledge', href: '/company' },
    ] },
    { id: 'history', label: 'History', href: '/app?view=history' },
  ];

  function build(header) {
    const page = header.dataset.page || '';
    const extra = header.querySelector('.pl-header-extra');
    if (extra) extra.remove();
    const active = (item) => item.id === page || (item.children || []).some((c) => c.id === page);
    const link = (i) => `<a href="${i.href}" class="${i.id === page ? 'is-active' : ''}"${i.id === page ? ' aria-current="page"' : ''}>${i.label}</a>`;
    const nav = ITEMS.map((i) => {
      if (!i.children) return link(i);
      return `<details class="pl-drop"><summary class="${active(i) ? 'is-active' : ''}">${i.label} <span aria-hidden="true">▾</span></summary>
        <div class="pl-drop-menu">${i.children.map((c) => `<a href="${c.href}" class="${c.id === page ? 'is-active' : ''}">${c.label}<span>${c.sub}</span></a>`).join('')}</div></details>`;
    }).join('');
    const landing = page === 'landing';          // marketing page: logo, Help and Sign in only
    header.innerHTML = `<a class="pl-logo" href="/"><span class="pl-logo-mark">◎</span> Pitchlight</a>
      ${landing ? '' : `<nav class="pl-nav" aria-label="Main">${nav}</nav>`}<span class="pl-header-spacer"></span>`;
    if (extra) header.appendChild(extra);
    header.insertAdjacentHTML('beforeend', `<a class="pl-help" href="/guide" style="${landing ? 'display:inline' : ''}">${landing ? 'How it works' : 'Help'}</a><span id="tester-slot"></span>`);

    // Only one dropdown open at a time; click elsewhere closes it.
    document.addEventListener('click', (e) => {
      header.querySelectorAll('details.pl-drop[open]').forEach((d) => { if (!d.contains(e.target)) d.removeAttribute('open'); });
    });
    // On the practice page History is an in-page panel, not a navigation.
    const hist = header.querySelector('a[href="/app?view=history"]');
    const histBtn = document.getElementById('btn-history');
    if (hist && histBtn && page === 'practice') {
      hist.addEventListener('click', (e) => { e.preventDefault(); histBtn.click(); });
    }
  }

  // One favicon for every page (the server has none, which logged a 404 each time).
  if (!document.querySelector('link[rel~="icon"]')) {
    const ic = document.createElement('link');
    ic.rel = 'icon';
    ic.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='12' fill='none' stroke='%231d4ed8' stroke-width='3'/%3E%3Ccircle cx='16' cy='16' r='5' fill='%231d4ed8'/%3E%3C/svg%3E";
    document.head.appendChild(ic);
  }

  const start = () => document.querySelectorAll('header.pl-header').forEach(build);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
