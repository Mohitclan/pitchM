/* Landing motion (GSAP + ScrollTrigger, loaded only on this page). The page is fully usable without it:
   content is visible by default, and .js-anim (set only when motion is allowed) hides it just before animating. */
(function () {
  const root = document.documentElement;
  if (!root.classList.contains('js-anim')) return;
  const bail = () => root.classList.remove('js-anim');
  if (!window.gsap || !window.ScrollTrigger) return bail();
  gsap.registerPlugin(ScrollTrigger);
  window.__ldMotion = true;

  // Hero copy: one staggered entrance
  gsap.fromTo('.ld-hero .ld-reveal', { y: 26, opacity: 0 },
    { y: 0, opacity: 1, duration: 0.8, ease: 'power3.out', stagger: 0.09, delay: 0.1, clearProps: 'transform' });

  // Scrolling away from the hero: the film drifts and settles, the copy lifts and softens
  gsap.to('.ld-film-inner', { scale: 1.07, yPercent: 5, ease: 'none',
    scrollTrigger: { trigger: '#hero', start: 'top top', end: 'bottom top', scrub: 0.6 } });
  gsap.to('.ld-hero-copy', { y: -50, opacity: 0.25, ease: 'none',
    scrollTrigger: { trigger: '#hero', start: '35% top', end: 'bottom top', scrub: 0.6 } });

  // Sections: reveal as they enter, once
  ScrollTrigger.batch('.ld-reveal-s', {
    start: 'top 88%', once: true,
    onEnter: (els) => gsap.fromTo(els, { y: 30, opacity: 0 },
      { y: 0, opacity: 1, duration: 0.7, ease: 'power3.out', stagger: 0.08, overwrite: true, clearProps: 'transform' }),
  });
  window.addEventListener('pagehide', () => ScrollTrigger.getAll().forEach((t) => t.kill()));
  window.addEventListener('load', () => ScrollTrigger.refresh());
})();
