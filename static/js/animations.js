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

  // ---------- Product gallery touch swipe (mobile) ----------
  var galleryMain = document.querySelector(".product-gallery__main");
  var galleryThumbs = document.querySelectorAll(".product-gallery__thumbs img");
  if (galleryMain && galleryThumbs.length > 1) {
    var touchStartX = 0;
    var touchEndX = 0;
    galleryMain.addEventListener("touchstart", function (e) {
      touchStartX = e.changedTouches[0].screenX;
    }, { passive: true });
    galleryMain.addEventListener("touchend", function (e) {
      touchEndX = e.changedTouches[0].screenX;
      var diff = touchEndX - touchStartX;
      if (Math.abs(diff) < 40) return; // ignore tiny swipes
      var currentIdx = Array.from(galleryThumbs).findIndex(function (t) {
        return t.classList.contains("active");
      });
      if (currentIdx === -1) currentIdx = 0;
      if (diff < 0) {
        // swipe left -> next image
        var nextIdx = Math.min(currentIdx + 1, galleryThumbs.length - 1);
        if (nextIdx !== currentIdx) {
          window.swapGalleryImage(galleryThumbs[nextIdx].src, galleryThumbs[nextIdx]);
        }
      } else {
        // swipe right -> previous image
        var prevIdx = Math.max(currentIdx - 1, 0);
        if (prevIdx !== currentIdx) {
          window.swapGalleryImage(galleryThumbs[prevIdx].src, galleryThumbs[prevIdx]);
        }
      }
    }, { passive: true });
  }

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

/* ============================================================
 Live search dropdown — instant results as you type
 ============================================================ */
(function () {
"use strict";
var input = document.getElementById("navSearchInput");
var dropdown = document.getElementById("searchDropdown");
if (!input || !dropdown) return;
var debounceTimer = null;
var activeIndex = -1;
var currentResults = [];

function render(results) {
  currentResults = results;
  activeIndex = -1;
  if (!results.length) {
    dropdown.innerHTML = '<div class="search-dropdown__empty">No products found. Try another search.</div>';
    dropdown.classList.add("open");
    return;
  }
  var html = results.map(function (r, i) {
    var img = r.image
      ? '<img class="search-dropdown__thumb" src="/static/uploads/' + r.image + '" alt="" loading="lazy">'
      : '<div class="search-dropdown__placeholder">' + (r.name[0] || "?") + "</div>";
    var cat = r.category ? '<div class="search-dropdown__cat">' + r.category + "</div>" : "";
    return '<a class="search-dropdown__item" href="/product/' + r.slug + '" role="option" data-index="' + i + '">' +
      img +
      '<div class="search-dropdown__info"><div class="search-dropdown__name">' + r.name + "</div>" + cat + "</div>" +
      '<div class="search-dropdown__price">&#8377;' + r.price.toLocaleString("en-IN") + "</div>" +
      "</a>";
  }).join("");
  html += '<div class="search-dropdown__footer">Press <kbd>Enter</kbd> for all results</div>';
  dropdown.innerHTML = html;
  dropdown.classList.add("open");
}

function close() {
  dropdown.classList.remove("open");
  activeIndex = -1;
}

input.addEventListener("input", function () {
  var q = input.value.trim();
  clearTimeout(debounceTimer);
  if (q.length < 2) { close(); return; }
  debounceTimer = setTimeout(function () {
    fetch("/api/search?q=" + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (data) { render(data.results || []); })
      .catch(function () { close(); });
  }, 180);
});

input.addEventListener("focus", function () {
  if (input.value.trim().length >= 2 && currentResults.length) {
    dropdown.classList.add("open");
  }
});

input.addEventListener("keydown", function (e) {
  var items = dropdown.querySelectorAll(".search-dropdown__item");
  if (!items.length) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeIndex = Math.min(activeIndex + 1, items.length - 1);
    updateActive(items);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeIndex = Math.max(activeIndex - 1, 0);
    updateActive(items);
  } else if (e.key === "Escape") {
    close();
    input.blur();
  }
});

function updateActive(items) {
  items.forEach(function (el, i) {
    el.classList.toggle("active", i === activeIndex);
    if (i === activeIndex) el.scrollIntoView({ block: "nearest" });
  });
}

document.addEventListener("click", function (e) {
  if (!e.target.closest(".nav__search")) close();
});
})();

/* ============================================================
 Keyboard shortcut: / to focus search
 ============================================================ */
(function () {
"use strict";
document.addEventListener("keydown", function (e) {
  if (e.key !== "/" || e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  var input = document.getElementById("navSearchInput");
  if (input) { e.preventDefault(); input.focus(); }
});
})();

/* ============================================================
 Image blur-up — add .loaded when images finish loading
 ============================================================ */
(function () {
"use strict";
function markLoaded(img) {
  if (img.complete) { img.classList.add("loaded"); return; }
  img.addEventListener("load", function () { img.classList.add("loaded"); });
  if (img.naturalWidth > 0) img.classList.add("loaded");
}
document.querySelectorAll(".card__image img, .product-gallery__main img").forEach(markLoaded);
})();

/* ============================================================
 Product card 3D tilt on hover (desktop only)
 ============================================================ */
(function () {
"use strict";
if (window.matchMedia("(hover: none), (pointer: coarse)").matches) return;
if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
document.querySelectorAll(".card").forEach(function (card) {
  card.addEventListener("mouseenter", function () { card.classList.add("tilting"); });
  card.addEventListener("mousemove", function (e) {
    if (!card.classList.contains("tilting")) return;
    var rect = card.getBoundingClientRect();
    var x = (e.clientX - rect.left) / rect.width - 0.5;
    var y = (e.clientY - rect.top) / rect.height - 0.5;
    card.style.transform = "translateY(-4px) perspective(800px) rotateX(" + (-y * 4) + "deg) rotateY(" + (x * 4) + "deg)";
  });
  card.addEventListener("mouseleave", function () {
    card.classList.remove("tilting");
    card.style.transform = "";
  });
});
})();

/* ============================================================
 Cart hover preview — fetch and show cart contents on hover
 ============================================================ */
(function () {
"use strict";
var wrap = document.querySelector(".nav__cart-wrap");
var preview = document.getElementById("cartPreview");
if (!wrap || !preview) return;
var loaded = false;
var loading = false;
var hideTimer = null;

function loadCart() {
  if (loading) return;
  loading = true;
  fetch("/api/cart/preview")
    .then(function (r) { return r.json(); })
    .then(function (data) { renderCart(data); loaded = true; loading = false; })
    .catch(function () { loading = false; });
}

function renderCart(data) {
  if (!data.items || !data.items.length) {
    preview.innerHTML = '<div class="cart-preview__empty">Your cart is empty.<br><a href="/#products" style="color:var(--grey-700);text-decoration:underline;">Browse products</a></div>';
    return;
  }
  var html = '<div class="cart-preview__header">Your Cart (' + data.count + ')</div>';
  html += data.items.map(function (it) {
    var img = it.image
      ? '<img class="cart-preview__thumb" src="/static/uploads/' + it.image + '" alt="">'
      : '<div class="cart-preview__placeholder">' + (it.name[0] || "?") + "</div>";
    return '<div class="cart-preview__item">' +
      img +
      '<div class="cart-preview__info"><div class="cart-preview__name">' + it.name + '</div><div class="cart-preview__qty">Qty: ' + it.quantity + '</div></div>' +
      '<div class="cart-preview__price">&#8377;' + it.line_total.toLocaleString("en-IN") + '</div>' +
      '</div>';
  }).join("");
  html += '<div class="cart-preview__footer">';
  html += '<div class="cart-preview__subtotal"><span>Subtotal</span><strong>&#8377;' + data.subtotal.toLocaleString("en-IN") + '</strong></div>';
  html += '<a href="/cart" class="btn btn--full btn--sm">View Cart & Checkout</a>';
  html += '</div>';
  preview.innerHTML = html;
}

wrap.addEventListener("mouseenter", function () {
  clearTimeout(hideTimer);
  if (!loaded) loadCart();
  preview.classList.add("open");
});
wrap.addEventListener("mouseleave", function () {
  hideTimer = setTimeout(function () { preview.classList.remove("open"); }, 200);
});
// Mobile: tap the cart icon to toggle preview
var cartLink = wrap.querySelector(".nav__cart");
if (cartLink) {
  cartLink.addEventListener("click", function (e) {
    if (window.matchMedia("(hover: none), (pointer: coarse)").matches) {
      e.preventDefault();
      if (preview.classList.contains("open")) {
        preview.classList.remove("open");
      } else {
        if (!loaded) loadCart();
        preview.classList.add("open");
        setTimeout(function() {
          if (preview.classList.contains("open")) {
            preview.classList.remove("open");
          }
        }, 5000);
      }
    }
  });
}
// Re-load cart after add-to-cart
document.addEventListener("submit", function (e) {
  if (e.target && e.target.classList && e.target.classList.contains("add-to-cart-form")) {
    loaded = false;
  }
});
})();

/* ============================================================
 Cookie consent banner
 ============================================================ */
(function () {
"use strict";
var banner = document.getElementById("cookieBanner");
if (!banner) return;
var choice = null;
try { choice = localStorage.getItem("cookieConsent"); } catch (e) {}
if (!choice) {
  setTimeout(function () { banner.classList.add("show"); }, 800);
}
window.dismissCookie = function (val) {
  try { localStorage.setItem("cookieConsent", val); } catch (e) {}
  banner.classList.remove("show");
};
})();

/* ============================================================
 Engagement system — confetti, scroll reveal, card tilt,
 magnetic buttons, heart burst, cart bounce
 ============================================================ */
(function () {
"use strict";

// ---- Confetti burst ----
window.fireConfetti = function (x, y, opts) {
opts = opts || {};
var count = opts.count || 24;
var colors = opts.colors || ["#0a0a0a", "#ffffff", "#8a8880", "#4a4944", "#d8d6d1"];
var i, el, angle, velocity, rot;
for (i = 0; i < count; i++) {
  el = document.createElement("div");
  el.className = "confetti-particle";
  el.style.left = x + "px";
  el.style.top = y + "px";
  el.style.background = colors[i % colors.length];
  if (Math.random() > 0.5) el.style.borderRadius = "999px";
  document.body.appendChild(el);
  angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.4;
  velocity = 80 + Math.random() * 120;
  var dx = Math.cos(angle) * velocity;
  var dy = Math.sin(angle) * velocity - 60;
  rot = (Math.random() - 0.5) * 720;
  el.animate([
    { transform: "translate(0,0) rotate(0deg)", opacity: 1 },
    { transform: "translate(" + dx + "px," + dy + "px) rotate(" + rot + "deg)", opacity: 0 }
  ], {
    duration: 700 + Math.random() * 400,
    easing: "cubic-bezier(0.16, 1, 0.3, 1)",
    fill: "forwards"
  });
  (function (node) {
    setTimeout(function () { node.remove(); }, 1200);
  })(el);
}
};

// ---- Scroll reveal via IntersectionObserver ----
var revealEls = document.querySelectorAll(".reveal, .stagger-item");
if (revealEls.length && "IntersectionObserver" in window) {
var revealObs = new IntersectionObserver(function (entries) {
  entries.forEach(function (entry) {
    if (entry.isIntersecting) {
      entry.target.classList.add(entry.target.classList.contains("reveal") ? "reveal--in" : "stagger--in");
      revealObs.unobserve(entry.target);
    }
  });
}, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
revealEls.forEach(function (el) { revealObs.observe(el); });
} else {
revealEls.forEach(function (el) {
  el.classList.add("reveal--in", "stagger--in");
});
}

// ---- Auto-tag grid children as stagger items ----
var grids = document.querySelectorAll(".grid");
grids.forEach(function (grid) {
var children = Array.prototype.slice.call(grid.children);
children.forEach(function (child, i) {
  if (child.classList.contains("stagger-item") || child.classList.contains("reveal")) return;
  child.classList.add("stagger-item");
  child.style.transitionDelay = (i % 4) * 0.08 + "s";
});
if ("IntersectionObserver" in window) {
  var gridObs = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("stagger--in");
        gridObs.unobserve(entry.target);
      }
    });
  }, { threshold: 0.08, rootMargin: "0px 0px -30px 0px" });
  Array.prototype.forEach.call(grid.children, function (c) { gridObs.observe(c); });
} else {
  Array.prototype.forEach.call(grid.children, function (c) { c.classList.add("stagger--in"); });
}
});

// ---- 3D card tilt on mousemove (desktop only) ----
if (matchMedia("(hover: hover) and (pointer: fine)").matches) {
document.querySelectorAll(".card").forEach(function (card) {
  card.classList.add("card-tilt");
  card.addEventListener("mousemove", function (e) {
    var rect = card.getBoundingClientRect();
    var px = (e.clientX - rect.left) / rect.width;
    var py = (e.clientY - rect.top) / rect.height;
    var rx = (py - 0.5) * -8;
    var ry = (px - 0.5) * 8;
    card.style.transform = "perspective(800px) rotateX(" + rx + "deg) rotateY(" + ry + "deg) translateY(-6px)";
  });
  card.addEventListener("mouseleave", function () {
    card.style.transform = "";
  });
});
}

// ---- Magnetic button effect (desktop only) ----
if (matchMedia("(hover: hover) and (pointer: fine)").matches) {
document.querySelectorAll(".hero .btn, .btn--primary, .btn--solid").forEach(function (btn) {
  btn.classList.add("magnetic-btn");
  btn.addEventListener("mousemove", function (e) {
    var rect = btn.getBoundingClientRect();
    var mx = e.clientX - rect.left - rect.width / 2;
    var my = e.clientY - rect.top - rect.height / 2;
    btn.style.transform = "translate(" + mx * 0.15 + "px," + my * 0.2 + "px)";
  });
  btn.addEventListener("mouseleave", function () {
    btn.style.transform = "";
  });
});
}

// ---- Heart burst on double-tap / double-click product cards ----
document.querySelectorAll(".card").forEach(function (card) {
var lastTap = 0;
card.addEventListener("click", function (e) {
  var now = Date.now();
  if (now - lastTap < 350) {
    var heart = document.createElement("div");
    heart.className = "heart-burst";
    heart.textContent = "♥";
    heart.style.left = (e.offsetX - 16) + "px";
    heart.style.top = (e.offsetY - 16) + "px";
    card.style.position = card.style.position || "relative";
    card.appendChild(heart);
    setTimeout(function () { heart.remove(); }, 600);
  }
  lastTap = now;
});
});

// ---- Pulse ring on add-to-cart button click ----
document.addEventListener("click", function (e) {
var btn = e.target.closest(".add-to-cart-form button[type=submit]");
if (!btn) return;
var ring = document.createElement("span");
ring.className = "btn-pulse-ring";
var computedRadius = getComputedStyle(btn).borderRadius;
ring.style.borderRadius = computedRadius || "2px";
btn.style.position = btn.style.position || "relative";
btn.appendChild(ring);
setTimeout(function () { ring.remove(); }, 600);
});

})();
