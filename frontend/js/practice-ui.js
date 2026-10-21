/**
 * Practice page: turns the "Practice with" dropdown into three-and-one clickable cards.
 * The <select id="practice-partner"> stays the source of truth (app.js reads and saves it); cards only set it.
 */
(function () {
  'use strict';
  const select = document.getElementById('practice-partner');
  const box = document.getElementById('partner-cards');
  if (!select || !box) return;

  const INFO = {
    '': ['Coach only', 'Live tips while you talk'],
    investor: ['Investor', 'Asks tough pitch questions'],
    interviewer: ['Interviewer', 'Asks interview questions'],
    client: ['Client', 'Asks buying questions and objections'],
  };

  function render() {
    box.innerHTML = [...select.options].map((o) => {
      const [title, sub] = INFO[o.value] || [o.textContent, ''];
      const on = select.value === o.value;
      return `<button type="button" class="partner-card${on ? ' is-on' : ''}" role="radio" aria-checked="${on}" data-v="${o.value}"><b>${title}</b><span>${sub}</span></button>`;
    }).join('');
  }
  box.addEventListener('click', (e) => {
    const card = e.target.closest('.partner-card');
    if (!card || select.disabled) return;
    select.value = card.dataset.v;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    render();
  });
  select.addEventListener('change', render);
  select.classList.add('pl-sr');          // visually hidden, still the value holder
  render();
})();
