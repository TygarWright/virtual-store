(function () {
  "use strict";

  // ---------- Scroll progress bar ----------
  var progress = document.querySelector(".scroll-progress");
  function updateProgress() {
    if (!progress) return;
    var h = document.documentElement;
    var max = h.scrollHeight - h.clientHeight;
    var pct = max > 0 ? (h.scrollTop / max) * 100 : 0;
    progress.style.width = pct + "%";
  }

  // ---------- Nav shrink on scroll ----------
  var nav = document.querySelector(".nav");
  function updateNav() {
    if (!nav) return;
    nav.classList.toggle("scrolled", window.scrollY > 10);
  }

  document.addEventListener(
    "scroll",
    function () {
      updateProgress();
      updateNav();
    },
    { passive: true }
  );
  updateProgress();
  updateNav();

  // ---------- Scroll-reveal ----------
  var revealEls = document.querySelectorAll(".reveal");
  if (revealEls.length) {
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-visible");
              io.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.15 }
      );
      revealEls.forEach(function (el) { io.observe(el); });
    } else {
      revealEls.forEach(function (el) { el.classList.add("is-visible"); });
    }
  }

  // ---------- Custom cursor + magnetic buttons (real mouse only) ----------
  var canHover = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  if (canHover) {
    var dot = document.createElement("div");
    dot.className = "cursor-dot";
    var ring = document.createElement("div");
    ring.className = "cursor-ring";
    document.body.appendChild(dot);
    document.body.appendChild(ring);
    document.body.classList.add("has-custom-cursor");

    var mouseX = window.innerWidth / 2,
      mouseY = window.innerHeight / 2,
      ringX = mouseX,
      ringY = mouseY;

    document.addEventListener("mousemove", function (e) {
      mouseX = e.clientX;
      mouseY = e.clientY;
      dot.style.left = mouseX + "px";
      dot.style.top = mouseY + "px";
    });

    (function loop() {
      ringX += (mouseX - ringX) * 0.18;
      ringY += (mouseY - ringY) * 0.18;
      ring.style.left = ringX + "px";
      ring.style.top = ringY + "px";
      requestAnimationFrame(loop);
    })();

    var hoverTargets = document.querySelectorAll("a, button, .card, input, textarea, select");
    hoverTargets.forEach(function (el) {
      el.addEventListener("mouseenter", function () { ring.classList.add("hovering"); });
      el.addEventListener("mouseleave", function () { ring.classList.remove("hovering"); });
    });

    document.querySelectorAll(".btn").forEach(function (btn) {
      btn.addEventListener("mousemove", function (e) {
        var rect = btn.getBoundingClientRect();
        var x = e.clientX - rect.left - rect.width / 2;
        var y = e.clientY - rect.top - rect.height / 2;
        btn.style.transform = "translate(" + x * 0.16 + "px, " + y * 0.3 + "px)";
      });
      btn.addEventListener("mouseleave", function () {
        btn.style.transform = "";
      });
    });
  }

  // ---------- Mobile hamburger menu ----------
  var navToggle = document.getElementById("navToggle");
  var mobileMenu = document.getElementById("mobileMenu");
  if (navToggle && mobileMenu) {
    navToggle.addEventListener("click", function () {
      var isOpen = mobileMenu.classList.toggle("open");
      navToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
      navToggle.setAttribute("aria-label", isOpen ? "Close menu" : "Open menu");
    });
    mobileMenu.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        mobileMenu.classList.remove("open");
        navToggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  // ---------- Product gallery crossfade ----------
  window.swapGalleryImage = function (src, thumbEl) {
    var main = document.getElementById("mainImage");
    if (!main) return;
    main.style.opacity = 0;
    setTimeout(function () {
      main.src = src;
      main.style.opacity = 1;
    }, 180);
    document.querySelectorAll(".product-gallery__thumbs img").forEach(function (t) {
      t.classList.remove("active");
    });
    if (thumbEl) thumbEl.classList.add("active");
  };
})();
