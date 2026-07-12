/* Firebase Phone Authentication (OTP) — sign in / create an account from
 * the landing page nav, or mid-checkout, without ever leaving the page.
 * Uses the Firebase compat SDK (loaded in base.html) so there's no build
 * step, consistent with the rest of this project. Silently does nothing if
 * FIREBASE_AUTH_ENABLED is false (site owner hasn't set up Firebase yet). */

let authRecaptchaVerifier = null;
let authConfirmationResult = null;
let authPendingIdToken = null;

function openAuthModal() {
  if (!window.FIREBASE_AUTH_ENABLED) return;
  const modal = document.getElementById("authModal");
  if (!modal) return;
  modal.classList.add("auth-modal--open");
  modal.setAttribute("aria-hidden", "false");
  authBackToPhone();
  ensureRecaptcha();
}

function closeAuthModal() {
  const modal = document.getElementById("authModal");
  if (!modal) return;
  modal.classList.remove("auth-modal--open");
  modal.setAttribute("aria-hidden", "true");
}

function ensureRecaptcha() {
  if (authRecaptchaVerifier || !window.firebase) return;
  authRecaptchaVerifier = new firebase.auth.RecaptchaVerifier("authRecaptcha", {
    size: "invisible",
  });
}

function authShowError(elId, msg) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = msg;
  el.style.display = msg ? "block" : "none";
}

async function authSendCode() {
  const phoneInput = document.getElementById("authPhoneInput");
  const phone = phoneInput.value.trim();
  authShowError("authPhoneError", "");

  if (!/^\+[1-9]\d{7,14}$/.test(phone)) {
    authShowError("authPhoneError", "Please enter a full phone number with country code, e.g. +919876543210.");
    return;
  }

  const btn = document.getElementById("authSendCodeBtn");
  btn.disabled = true;
  btn.textContent = "Sending…";

  try {
    ensureRecaptcha();
    authConfirmationResult = await firebase.auth().signInWithPhoneNumber(phone, authRecaptchaVerifier);
    document.getElementById("authPhoneDisplay").textContent = phone;
    document.getElementById("authStepPhone").style.display = "none";
    document.getElementById("authStepCode").style.display = "block";
  } catch (err) {
    authShowError("authPhoneError", "Could not send a code to that number. Please check it and try again.");
    if (authRecaptchaVerifier) {
      authRecaptchaVerifier.render().then((widgetId) => {
        if (window.grecaptcha) window.grecaptcha.reset(widgetId);
      });
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Send Code";
  }
}

function authBackToPhone() {
  document.getElementById("authStepPhone").style.display = "block";
  document.getElementById("authStepCode").style.display = "none";
  document.getElementById("authStepDetails").style.display = "none";
  authShowError("authPhoneError", "");
  authShowError("authCodeError", "");
}

async function authVerifyCode() {
  const code = document.getElementById("authCodeInput").value.trim();
  authShowError("authCodeError", "");
  if (!code) {
    authShowError("authCodeError", "Please enter the 6-digit code.");
    return;
  }
  const btn = document.getElementById("authVerifyBtn");
  btn.disabled = true;
  btn.textContent = "Verifying…";

  try {
    const result = await authConfirmationResult.confirm(code);
    authPendingIdToken = await result.user.getIdToken();
    document.getElementById("authStepCode").style.display = "none";
    document.getElementById("authStepDetails").style.display = "block";
    if (result.user.displayName) document.getElementById("authNameInput").value = result.user.displayName;
    if (result.user.email) document.getElementById("authEmailInput").value = result.user.email;
  } catch (err) {
    authShowError("authCodeError", "That code didn't match. Please check it and try again.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Verify & Continue";
  }
}

async function authFinish() {
  const name = document.getElementById("authNameInput").value.trim();
  const email = document.getElementById("authEmailInput").value.trim();
  authShowError("authDetailsError", "");

  if (!authPendingIdToken) {
    authShowError("authDetailsError", "Something went wrong — please start over.");
    return;
  }

  const btn = document.getElementById("authFinishBtn");
  btn.disabled = true;
  btn.textContent = "Please wait…";

  try {
    const res = await fetch("/auth/phone/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": window.getCsrfToken() },
      body: JSON.stringify({ id_token: authPendingIdToken, name, email }),
    });
    const data = await res.json();
    if (!res.ok) {
      authShowError("authDetailsError", data.error || "Something went wrong. Please try again.");
      return;
    }
    authPendingIdToken = null;
    closeAuthModal();
    // Reflect the signed-in state immediately: swap the nav "Sign in" link
    // for the customer's name, and prefill any checkout form on this page.
    document.querySelectorAll("[data-auth-signed-out]").forEach((el) => (el.style.display = "none"));
    document.querySelectorAll("[data-auth-signed-in]").forEach((el) => {
      el.style.display = "";
      el.textContent = data.name || data.phone || "Account";
    });
    const nameField = document.getElementById("buyerName") || document.getElementById("cartBuyerName");
    const emailField = document.getElementById("buyerEmail") || document.getElementById("cartBuyerEmail");
    const phoneField = document.getElementById("buyerPhone") || document.getElementById("cartBuyerPhone");
    if (nameField && data.name && !nameField.value) nameField.value = data.name;
    if (emailField && data.email && !emailField.value) emailField.value = data.email;
    if (phoneField && data.phone && !phoneField.value) phoneField.value = data.phone;
    if (window.showToast) window.showToast(`Signed in as ${data.name || data.phone}`, "success");
  } catch (err) {
    authShowError("authDetailsError", "Network error. Please try again.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Create My Account";
  }
}

document.addEventListener("DOMContentLoaded", function () {
  if (window.FIREBASE_CONFIG && window.FIREBASE_CONFIG.apiKey && window.firebase) {
    firebase.initializeApp(window.FIREBASE_CONFIG);
    window.FIREBASE_AUTH_ENABLED = true;
  }
});
