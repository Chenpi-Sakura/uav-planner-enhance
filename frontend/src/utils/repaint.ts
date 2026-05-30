/**
 * Force a browser repaint. This is needed to work around
 * a Chromium bug where backdrop-filter elements don't
 * visually update until a mousemove event.
 */
export function forceRepaint() {
  // Read then write a layout property to force synchronous reflow
  document.body.style.opacity = '0.999';
  // Force the browser to compute the style change
  void document.body.offsetHeight;
  // Immediately restore
  requestAnimationFrame(() => {
    document.body.style.opacity = '1';
  });
}
