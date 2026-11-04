/**
 * Ghost Motion Flagship Interactive Engine
 * Controls signature interactions: before/after split slider with mouse, touch, and ARIA keyboard support.
 */

document.addEventListener("DOMContentLoaded", () => {
  initBeforeAfterSlider();
});

export function initBeforeAfterSlider() {
  const container = document.querySelector(".before-after-container");
  const afterLayer = document.querySelector(".image-layer.after");
  const handle = document.querySelector(".slider-handle");

  if (!container || !afterLayer || !handle) {
    return;
  }

  let isDragging = false;
  let currentPercentage = 50;

  function updateSlider(percentage) {
    // Clamp between 0 and 100
    const clamped = Math.max(0, Math.min(100, percentage));
    currentPercentage = clamped;

    afterLayer.style.clipPath = `polygon(0 0, ${clamped}% 0, ${clamped}% 100%, 0 100%)`;
    handle.style.left = `${clamped}%`;
    handle.setAttribute("aria-valuenow", Math.round(clamped).toString());
  }

  function getPositionX(e) {
    if (e.touches && e.touches.length > 0) {
      return e.touches[0].clientX;
    }
    return e.clientX;
  }

  function handleMove(e) {
    if (!isDragging) return;
    const rect = container.getBoundingClientRect();
    const x = getPositionX(e) - rect.left;
    const percentage = (x / rect.width) * 100;
    updateSlider(percentage);
  }

  function startDrag(e) {
    isDragging = true;
    handleMove(e);
  }

  function stopDrag() {
    isDragging = false;
  }

  // Pointer & Touch Events
  container.addEventListener("mousedown", startDrag);
  window.addEventListener("mousemove", handleMove);
  window.addEventListener("mouseup", stopDrag);

  container.addEventListener("touchstart", startDrag, { passive: true });
  window.addEventListener("touchmove", handleMove, { passive: true });
  window.addEventListener("touchend", stopDrag);

  // Keyboard Accessibility
  handle.addEventListener("keydown", (e) => {
    let delta = 0;
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
      delta = -5;
    } else if (e.key === "ArrowRight" || e.key === "ArrowUp") {
      delta = 5;
    } else if (e.key === "Home") {
      delta = -currentPercentage;
    } else if (e.key === "End") {
      delta = 100 - currentPercentage;
    }

    if (delta !== 0) {
      e.preventDefault();
      updateSlider(currentPercentage + delta);
    }
  });

  // Initialize at 50%
  updateSlider(50);
}
