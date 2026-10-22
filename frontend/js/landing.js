/* "See it in action" film: a silent, looping render of the real flow. It starts only when it scrolls into view,
   picks the lightest source that fits, and pauses when off screen. Without video the poster stays. */
(function () {
  const video = document.getElementById('ld-video');
  const card = document.getElementById('ld-filmcard');
  if (!video || !card) return;
  const conn = navigator.connection || {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const slow = conn.saveData || /(^|-)2g$|3g/.test(conn.effectiveType || '');
  if (reduced || slow || !('IntersectionObserver' in window)) return;     // poster only

  let loaded = false;
  const go = () => video.play().catch(() => { /* autoplay blocked: poster stays */ });
  function load() {
    loaded = true;
    const small = matchMedia('(max-width: 980px)').matches;
    video.src = (small ? '/static/media/hero-mobile.mp4' : (video.canPlayType('video/webm; codecs="vp9"') ? '/static/media/hero.webm' : '/static/media/hero.mp4')) + '?v=20261027e';
    video.addEventListener('playing', () => video.classList.add('is-on'), { once: true });
  }
  new IntersectionObserver((e) => {
    if (e[0].isIntersecting) { if (!loaded) load(); go(); } else video.pause();
  }, { threshold: 0.2 }).observe(card);
  document.addEventListener('visibilitychange', () => { if (document.hidden) video.pause(); });
})();
