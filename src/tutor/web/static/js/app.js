// Motion layer and small behaviours for the dashboard (spec section 10, plan Task 15).
// The server renders every element in its final state; this file only adds movement on top,
// so reduced motion, an old browser or an error here leaves a complete page.
// Allowed properties: transform, opacity, stroke-dashoffset. Text is set with textContent only.

const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)";
const SPRING_CURVE =
  "linear(0, 0.009, 0.035 2.1%, 0.141, 0.281 6.7%, 0.723 12.9%, 0.938 16.7%, 1.017, 1.077, " +
  "1.121, 1.149 24.3%, 1.159, 1.163, 1.161, 1.154 29.9%, 1.129 32.8%, 1.051 39.6%, " +
  "1.017 43.1%, 0.991, 0.977 51%, 0.974 53.8%, 0.975 57.1%, 0.997 69.8%, 1.003 76.9%, 1)";
const SPRING = CSS.supports("animation-timing-function", "linear(0, 1)") ? SPRING_CURVE : EASE_OUT;
const SVG_NS = "http://www.w3.org/2000/svg";
const LEAF_PATH = "M0 -9 C 6 -4 6 4 0 9 C -6 4 -6 -4 0 -9 Z";

const reducedMotion = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
  document.body.dataset.reduceMotion !== undefined;

const liveRegion = () => document.getElementById("live");

function announce(text) {
  const region = liveRegion();
  if (!region || !text) return;
  region.textContent = "";
  window.setTimeout(() => {
    region.textContent = text;
  }, 50);
}

// Each element animates at most once, even if several HTMX swaps re-scan the page.
function claim(el) {
  if (el.hasAttribute("data-motion-done")) return false;
  el.setAttribute("data-motion-done", "");
  return true;
}

function whenVisible(el, run) {
  if (!("IntersectionObserver" in window)) {
    run();
    return;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect();
        run();
      }
    },
    { threshold: 0.3 },
  );
  observer.observe(el);
}

function countUp(el) {
  const target = Number.parseFloat(el.dataset.countTo);
  if (!Number.isFinite(target)) return;
  const decimals = Number.parseInt(el.dataset.countDecimals || "0", 10);
  const finalText = el.textContent;
  const format = new Intl.NumberFormat(document.documentElement.lang || "es-MX", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const start = performance.now();
  const step = (now) => {
    const t = Math.min((now - start) / 700, 1);
    const eased = 1 - (1 - t) ** 3;
    el.textContent = t < 1 ? format.format(target * eased) : finalText;
    if (t < 1) window.requestAnimationFrame(step);
  };
  window.requestAnimationFrame(step);
}

function draw(path) {
  const length = path.getTotalLength();
  const dash = `${length} ${length}`;
  path.animate(
    [
      { strokeDasharray: dash, strokeDashoffset: length },
      { strokeDasharray: dash, strokeDashoffset: 0 },
    ],
    { duration: 800, easing: EASE_OUT },
  );
  path
    .closest("svg")
    ?.querySelectorAll(".chart__target")
    .forEach((line) =>
      line.animate([{ opacity: 0 }, { opacity: 1 }], {
        duration: 400,
        delay: 400,
        easing: "ease-out",
        fill: "backwards",
      }),
    );
}

function pop(container) {
  container.querySelectorAll(".stamp--used").forEach((stamp, index) => {
    stamp.animate(
      [
        { transform: "scale(0.6)", opacity: 0 },
        { transform: "scale(1)", opacity: 1 },
      ],
      { duration: 500, delay: Math.min(index * 60, 400), easing: SPRING, fill: "backwards" },
    );
  });
}

function strike(item, index) {
  const offset = Math.min(index * 120, 600);
  item.querySelector(".said")?.animate([{ opacity: 0.4 }, { opacity: 1 }], {
    duration: 300,
    delay: offset,
    easing: "ease-out",
    fill: "backwards",
  });
  item.querySelector(".correct")?.animate(
    [
      { opacity: 0, transform: "translateX(-8px)" },
      { opacity: 1, transform: "none" },
    ],
    { duration: 500, delay: offset + 350, easing: EASE_OUT, fill: "backwards" },
  );
}

function grow(rect) {
  rect.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], {
    duration: 600,
    easing: EASE_OUT,
  });
}

function celebrate(el) {
  const box = el.getBoundingClientRect();
  const cx = box.left + box.width / 2;
  const cy = box.top + box.height / 2;
  const layer = document.createElement("div");
  layer.className = "celebrate";
  layer.setAttribute("aria-hidden", "true");
  const svg = document.createElementNS(SVG_NS, "svg");
  layer.append(svg);
  document.body.append(layer);
  const kinds = ["a", "b", "c"];
  for (let i = 0; i < 14; i += 1) {
    const leaf = document.createElementNS(SVG_NS, "path");
    leaf.setAttribute("d", LEAF_PATH);
    leaf.setAttribute("class", `celebrate__leaf celebrate__leaf--${kinds[i % 3]}`);
    svg.append(leaf);
    const angle = (i / 14) * Math.PI * 2 + Math.random() * 0.4;
    const distance = 70 + Math.random() * 70;
    const dx = Math.cos(angle) * distance;
    const dy = Math.sin(angle) * distance - 30;
    const turn = Math.round(Math.random() * 240 - 120);
    leaf.animate(
      [
        { transform: `translate(${cx}px, ${cy}px) scale(0.4) rotate(0deg)`, opacity: 1 },
        {
          transform: `translate(${cx + dx}px, ${cy + dy}px) scale(1) rotate(${turn}deg)`,
          opacity: 0,
        },
      ],
      { duration: 900 + Math.random() * 250, easing: EASE_OUT, fill: "forwards" },
    );
  }
  window.setTimeout(() => layer.remove(), 1200);
}

// ---- install prompt -----------------------------------------------------

let deferredPrompt = null;

const isStandalone = () =>
  window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;

const isIosSafari = () => {
  const ua = window.navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  return ios && /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
};

function showInstall(root) {
  if (isStandalone()) return;
  root.querySelectorAll("[data-install-card]").forEach((card) => {
    const button = card.querySelector("[data-install]");
    const guide = card.querySelector("[data-ios-install]");
    if (deferredPrompt && button) {
      button.hidden = false;
      card.hidden = false;
    } else if (guide && isIosSafari()) {
      guide.hidden = false;
      card.hidden = false;
    }
  });
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredPrompt = event;
  showInstall(document);
});

window.addEventListener("appinstalled", () => {
  deferredPrompt = null;
  document.querySelectorAll("[data-install-card]").forEach((card) => {
    card.hidden = true;
  });
});

// ---- delegated clicks: copy and install --------------------------------

document.addEventListener("click", async (event) => {
  const copyButton = event.target.closest("[data-copy]");
  if (copyButton) {
    const region = liveRegion();
    try {
      await navigator.clipboard.writeText(copyButton.dataset.copy);
      const copied = region?.dataset.copied || "Copiado";
      // The label is the last <span>, or the button itself when it holds only text.
      const label =
        copyButton.querySelector("span:last-of-type") ??
        (copyButton.children.length === 0 ? copyButton : null);
      if (label) {
        const original = label.textContent;
        label.textContent = copied;
        window.setTimeout(() => {
          label.textContent = original;
        }, 1500);
      }
      announce(copied);
    } catch {
      announce(region?.dataset.copyFailed);
    }
    return;
  }
  const installButton = event.target.closest("[data-install]");
  if (installButton && deferredPrompt) {
    const prompt = deferredPrompt;
    deferredPrompt = null;
    await prompt.prompt();
    await prompt.userChoice;
    installButton.hidden = true;
  }
});

// ---- wiring -------------------------------------------------------------

function init(root) {
  showInstall(root);
  if (reducedMotion()) return;
  root.querySelectorAll("[data-count-to]").forEach((el) => {
    if (claim(el)) whenVisible(el, () => countUp(el));
  });
  root.querySelectorAll("path[data-draw]").forEach((path) => {
    if (claim(path)) whenVisible(path, () => draw(path));
  });
  root.querySelectorAll("[data-pop]").forEach((container) => {
    if (claim(container)) whenVisible(container, () => pop(container));
  });
  root.querySelectorAll("[data-strike]").forEach((item, index) => {
    if (claim(item)) whenVisible(item, () => strike(item, index));
  });
  root.querySelectorAll("rect[data-grow]").forEach((rect) => {
    if (claim(rect)) whenVisible(rect, () => grow(rect));
  });
  root.querySelectorAll("[data-celebrate]").forEach((el) => {
    if (claim(el)) celebrate(el);
  });
}

document.addEventListener("htmx:afterSettle", () => init(document));
document.body.addEventListener("announce", (event) => announce(event.detail?.value));

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

init(document);
