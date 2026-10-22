/* Points, Facts and Commitments share one tabbed panel. meeting.js still shows/hides each panel as before;
   this only draws a tab for each visible one and shows the chosen tab. */
(function () {
  const tabs = document.getElementById('mt-tabs');
  const box = document.getElementById('mt-panels');
  if (!tabs || !box) return;
  const NAMES = { 'mt-points-panel': 'Points', 'mt-facts-panel': 'Facts', 'mt-commit-panel': 'Commitments' };
  const COUNTS = { 'mt-points-panel': 'mt-points-count', 'mt-facts-panel': 'mt-facts-count', 'mt-commit-panel': 'mt-commit-count' };
  let chosen = null;

  function refresh() {
    const live = [...box.children].filter((p) => NAMES[p.id] && !p.classList.contains('hidden'));
    if (!live.some((p) => p.id === chosen)) chosen = live[0] ? live[0].id : null;
    box.querySelectorAll('.mt-points-panel').forEach((p) => p.classList.toggle('is-tab-on', p.id === chosen));
    const html = live.length < 2 ? '' : live.map((p) => {
      const n = (document.getElementById(COUNTS[p.id]) || {}).textContent || '';
      return `<button type="button" class="mt-tab${p.id === chosen ? ' is-on' : ''}" role="tab" data-p="${p.id}" aria-selected="${p.id === chosen}">${NAMES[p.id]} ${n}</button>`;
    }).join('');
    if (tabs.innerHTML !== html) tabs.innerHTML = html;
    if (live.length === 1) live[0].classList.add('is-tab-on');
  }
  tabs.addEventListener('click', (e) => {
    const b = e.target.closest('.mt-tab');
    if (b) { chosen = b.dataset.p; refresh(); }
  });
  new MutationObserver(refresh).observe(box, { attributes: true, subtree: true, childList: true, characterData: true, attributeFilter: ['class'] });
  refresh();
})();
