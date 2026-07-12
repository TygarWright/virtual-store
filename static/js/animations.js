// Shared helper: reads the CSRF token from the page's meta tag so any
// fetch() call across the site can include it as a header.
window.getCsrfToken = function () {
  var meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute("content") : "";
};

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
    var closeMenu = function () {
      mobileMenu.classList.remove("open");
      navToggle.setAttribute("aria-expanded", "false");
      navToggle.setAttribute("aria-label", "Open menu");
      document.body.classList.remove("menu-open");
    };
    var openMenu = function () {
      mobileMenu.classList.add("open");
      navToggle.setAttribute("aria-expanded", "true");
      navToggle.setAttribute("aria-label", "Close menu");
      document.body.classList.add("menu-open");
    };
    navToggle.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (mobileMenu.classList.contains("open")) closeMenu();
      else openMenu();
    });
    mobileMenu.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", closeMenu);
    });
    // Tapping/clicking anywhere outside the open menu closes it
    document.addEventListener("click", function (e) {
      if (
        mobileMenu.classList.contains("open") &&
        !mobileMenu.contains(e.target) &&
        !navToggle.contains(e.target)
      ) {
        closeMenu();
      }
    });
    // Escape closes it too
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && mobileMenu.classList.contains("open")) closeMenu();
    });
    // Closing the mobile viewport back out to desktop width shouldn't leave
    // the menu stuck open behind the (now hidden) toggle button
    window.addEventListener("resize", function () {
      if (window.innerWidth > 900 && mobileMenu.classList.contains("open")) closeMenu();
    });
  }

  // ---------- Back to top button ----------
  var backToTop = document.getElementById("backToTop");
  if (backToTop) {
    if (window.scrollY > 400) backToTop.classList.add("visible");
    window.addEventListener("scroll", function () {
      backToTop.classList.toggle("visible", window.scrollY > 400);
    }, { passive: true });
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

  // ---------- Subtle keystroke pulse ----------
  // A tiny, elegant flicker of the scroll-progress bar every time a key is
  // pressed — physical keyboard, phone keyboard, anything that fires
  // keydown. Throttled so fast typing doesn't spam animations.
  if (progress) {
    var keyPulseReady = true;
    document.addEventListener("keydown", function () {
      if (!keyPulseReady) return;
      keyPulseReady = false;
      progress.classList.add("key-pulse");
      setTimeout(function () {
        progress.classList.remove("key-pulse");
        keyPulseReady = true;
      }, 260);
    });
  }

  // ---------- Typing spark ----------
  // Wakes a small dot cluster beside the search field the moment someone
  // starts typing, gives each real keystroke its own quick pulse, and lets
  // the whole thing fade back to rest ~700ms after typing stops (or the
  // field loses focus) rather than snapping off abruptly.
  document.querySelectorAll(".nav__search").forEach(function (form) {
    var input = form.querySelector("input");
    var spark = form.querySelector(".type-spark");
    if (!input || !spark) return;
    var dots = spark.querySelectorAll("i");
    var idleTimer = null;
    var dotIndex = 0;
    var ignoredKeys = { Shift: 1, Control: 1, Alt: 1, Meta: 1, Tab: 1, CapsLock: 1, Escape: 1 };

    function comeAlive() {
      form.classList.add("is-typing");
    }
    function dieGracefully() {
      form.classList.remove("is-typing");
    }
    function kick() {
      var dot = dots[dotIndex % dots.length];
      dotIndex++;
      dot.classList.remove("spark-kick");
      void dot.offsetWidth; // restart the animation even if this dot just fired
      dot.classList.add("spark-kick");
      setTimeout(function () { dot.classList.remove("spark-kick"); }, 320);
    }

    input.addEventListener("keydown", function (e) {
      if (ignoredKeys[e.key]) return;
      comeAlive();
      kick();
      clearTimeout(idleTimer);
      idleTimer = setTimeout(dieGracefully, 700);
    });
    input.addEventListener("blur", function () {
      clearTimeout(idleTimer);
      dieGracefully();
    });
  });
})();
