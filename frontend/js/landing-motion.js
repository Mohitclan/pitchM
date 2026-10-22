/* Landing motion (GSAP + ScrollTrigger, loaded only on this page). The page is fully usable without it:
   content is visible by default, and .js-anim (set only when motion is allowed) hides it just before animating. */
(function () {
  const root = document.documentElement;
  if (!root.classList.contains('js-anim')) return;
  const bail = () => root.classList.remove('js-anim');
  if (!window.gsap || !window.ScrollTrigger) return bail();
  gsap.registerPlugin(ScrollTrigger);
  window.__ldMotion = true;

  // Hero: copy first, then the meeting window, then the assistant panel and its cards
  const tl = gsap.timeline({ defaults: { ease: 'power3.out' }, delay: 0.1 });
  tl.fromTo('.ld-hero .ld-reveal', { y: 26, opacity: 0 }, { y: 0, opacity: 1, duration: 0.8, stagger: 0.09, clearProps: 'transform' })
    .fromTo('.dm-frame', { y: 50, opacity: 0, scale: 0.96 }, { y: 0, opacity: 1, scale: 1, duration: 1.1, clearProps: 'transform' }, 0.25)
    .fromTo('.dm-q', { y: 14, opacity: 0 }, { y: 0, opacity: 1, duration: 0.6 }, 0.95)
    .fromTo('.dm-panel', { x: 36, opacity: 0 }, { x: 0, opacity: 1, duration: 0.8 }, 1.15)
    .fromTo('.dm-card', { y: 18, opacity: 0 }, { y: 0, opacity: 1, duration: 0.6, stagger: 0.25 }, 1.55);

  // Slow, gentle drift of the background light
  gsap.to('.ld-aura.a', { x: -50, y: 30, duration: 11, repeat: -1, yoyo: true, ease: 'sine.inOut' });
  gsap.to('.ld-aura.b', { x: 40, y: -30, duration: 13, repeat: -1, yoyo: true, ease: 'sine.inOut' });
  gsap.to('.ld-aura.c', { x: 30, y: 40, duration: 15, repeat: -1, yoyo: true, ease: 'sine.inOut' });
  gsap.to('.rb-a', { y: -6, duration: 7, repeat: -1, yoyo: true, ease: 'sine.inOut' });

  // Leaving the hero: the demo lifts a little and the copy softens
  gsap.to('.dm', { y: -36, ease: 'none', scrollTrigger: { trigger: '#hero', start: 'top top', end: 'bottom top', scrub: 0.6 } });
  gsap.to('.ld-hero-copy', { opacity: 0.35, ease: 'none', scrollTrigger: { trigger: '#hero', start: '40% top', end: 'bottom top', scrub: 0.6 } });

  // Section reveals, once
  ScrollTrigger.batch('.ld-reveal-s', {
    start: 'top 88%', once: true,
    onEnter: (els) => gsap.fromTo(els, { y: 30, opacity: 0 },
      { y: 0, opacity: 1, duration: 0.7, ease: 'power3.out', stagger: 0.08, overwrite: true, clearProps: 'transform' }),
  });
  // The three steps light up one after another as the card comes into view
  gsap.fromTo('.ld-flowsteps li', { x: -16, opacity: 0.35 }, { x: 0, opacity: 1, duration: 0.6, stagger: 0.25, ease: 'power2.out',
    scrollTrigger: { trigger: '.ld-flowcard', start: 'top 75%', once: true }, clearProps: 'transform' });

  window.addEventListener('pagehide', () => ScrollTrigger.getAll().forEach((t) => t.kill()));
  window.addEventListener('load', () => ScrollTrigger.refresh());
})();
