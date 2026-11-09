// A card marked data-fill reaches down to the bottom of the first screen of
// the page around its frame on a wide screen, as the design's cards do, and
// shows the rows that fit whole, three at the least; a timer marked
// data-since counts on from when its part was drawn.
(() => {
  const fill = () => {
    for (const card of document.querySelectorAll("[data-fill]")) {
      const rows = [...card.querySelectorAll(".turn, .feed-item")];
      card.style.height = "";
      for (const row of rows) row.hidden = false;
      if (document.documentElement.dataset.layout !== "wide" || !window.frameElement) continue;
      const top =
        window.frameElement.getBoundingClientRect().top + window.parent.scrollY + card.getBoundingClientRect().top;
      const least = rows.length > 3 ? rows[2].offsetTop + rows[2].offsetHeight + 6 : card.offsetHeight;
      card.style.height = `${Math.max(window.parent.innerHeight - top - 24, least)}px`;
      const bottom = card.getBoundingClientRect().bottom;
      for (const row of rows) row.hidden = row.getBoundingClientRect().bottom > bottom;
    }
  };
  const tick = () => {
    for (const part of document.querySelectorAll("[data-now]")) {
      part.dataset.drawn ||= String(Date.now());
      const now = Number(part.dataset.now) + Date.now() - Number(part.dataset.drawn);
      for (const timer of part.querySelectorAll("[data-since]")) {
        const seconds = Math.max(0, Math.floor((now - Number(timer.dataset.since)) / 1000));
        const pad = (n) => String(n).padStart(2, "0");
        const hours = Math.floor(seconds / 3600);
        const rest = `${pad(Math.floor((seconds % 3600) / 60))}:${pad(seconds % 60)}`;
        timer.textContent = hours ? `${hours}:${rest}` : rest;
      }
    }
  };
  addEventListener("DOMContentLoaded", () => {
    fill();
    if (window.frameElement) window.parent.addEventListener("resize", fill);
    setInterval(tick, 1000);
  });
  document.addEventListener("htmx:after:settle", fill);
})();
