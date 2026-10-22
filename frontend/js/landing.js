/* Landing hero film: picks the lightest source that fits, starts it after first paint,
   and stops it when it is off screen. Without video the poster (already in the page) stays. */
(function () {
  const video = document.getElementById('ld-video');
  const hero = document.getElementById('hero');
  if (!video || !hero) return;
  const conn = navigator.connection || {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const slow = conn.saveData || /(^|-)2g$|3g/.test(conn.effectiveType || '');
  if (reduced || slow) return;                       // poster only

  function pick() {
    if (matchMedia('(max-width: 960px)').matches) return '/static/media/hero-mobile.mp4';
    return video.canPlayType('video/webm; codecs="vp9"') ? '/static/media/hero.webm' : '/static/media/hero.mp4';
  }
  function start() {
    video.src = pick() + '?v=20261026d';
    video.addEventListener('playing', () => video.classList.add('is-on'), { once: true });
    const go = () => video.play().catch(() => { /* autoplay blocked: poster stays */ });
    go();
    if ('IntersectionObserver' in window) {
      new IntersectionObserver((e) => (e[0].isIntersecting ? go() : video.pause()), { threshold: 0.05 }).observe(hero);
    }
    document.addEventListener('visibilitychange', () => (document.hidden ? video.pause() : go()));
  }
  const later = window.requestIdleCallback || ((f) => setTimeout(f, 200));
  if (document.readyState === 'complete') later(start); else window.addEventListener('load', () => later(start), { once: true });
})();
