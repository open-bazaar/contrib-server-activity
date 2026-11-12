// A card marked data-fill reaches down to the bottom of the first screen of
// the page around its frame on a wide screen, as the design's cards do, and
// shows the rows that fit whole, three at the least; a timer marked
// data-since counts on from when its part was drawn. A turn's steps keep
// the newest in view, and the whole record the line it was read at.
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
  // a turn's steps scroll within its card: the newest in view while the
  // reader is at it, the place kept once they scroll up, each time the cards
  // are drawn again
  const newest = () => {
    for (const trail of document.querySelectorAll(".trail")) trail.scrollTop = trail.scrollHeight;
  };
  let scrolled = null;
  document.addEventListener("htmx:before:swap", (event) => {
    if (event.target.id !== "live") return;
    scrolled = new Map();
    for (const trail of event.target.querySelectorAll(".trail")) {
      if (Math.ceil(trail.scrollTop + trail.clientHeight) < trail.scrollHeight) scrolled.set(trail.dataset.turn, trail.scrollTop);
    }
  });
  // the cards are drawn anew in their place, so this is told the document
  document.addEventListener("htmx:after:swap", () => {
    if (!scrolled) return;
    newest();
    for (const trail of document.querySelectorAll(".trail")) {
      if (scrolled.has(trail.dataset.turn)) trail.scrollTop = scrolled.get(trail.dataset.turn);
    }
    scrolled = null;
  });
  // the whole record takes what comes in at its top: a reader at the top sees
  // it, one further down keeps the line they were at. Its frame is as tall as
  // it is, so what scrolls is the first page up whose frame is not, scrolled
  // on by what came in above that line once the frames between are as tall as
  // they are drawn now
  const scroller = () => {
    let at = window;
    let top = 0;
    while (at.frameElement?.hasAttribute("data-fit")) {
      top += at.frameElement.getBoundingClientRect().top;
      at = at.parent;
    }
    return { at, top };
  };
  let reading = null;
  document.addEventListener("htmx:before:swap", (event) => {
    if (event.target.id !== "follow") return;
    const { top } = scroller();
    const rows = document.getElementById("rows");
    reading = null;
    if (top + rows.getBoundingClientRect().top >= 0) return;
    const line = [...rows.querySelectorAll(".ev")].find((row) => top + row.getBoundingClientRect().bottom > 0);
    if (line) reading = { id: line.dataset.id, top: line.getBoundingClientRect().top };
  });
  document.addEventListener("htmx:after:swap", (event) => {
    if (event.target.id !== "rows" || !reading) return;
    const line = event.target.querySelector(`.ev[data-id="${reading.id}"]`);
    const was = reading.top;
    reading = null;
    if (!line) return;
    for (let at = window; at.frameElement?.hasAttribute("data-fit"); at = at.parent) {
      at.frameElement.style.height = `${at.document.documentElement.offsetHeight}px`;
    }
    scroller().at.scrollBy(0, line.getBoundingClientRect().top - was);
  });
  addEventListener("DOMContentLoaded", () => {
    fill();
    newest();
    if (window.frameElement) window.parent.addEventListener("resize", fill);
    setInterval(tick, 1000);
  });
  document.addEventListener("htmx:after:settle", fill);
})();
