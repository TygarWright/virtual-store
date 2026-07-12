/* Self-contained Phone OTP Authentication — no Firebase, no external service.
 * Generates a 6-digit code server-side, stores it in the DB, verifies it,
 * and creates/updates the customer account. In dev mode the code is shown
 * in the modal so you can test the full flow without an SMS gateway. */

let authPendingPhone = null;
let authTimerInterval = null;
let authResendSeconds = 0;

function openAuthModal() {
  const modal = document.getElementById("authModal");
  if (!modal) return;
  modal.classList.add("auth-modal--open");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("auth-modal-open");
  authGoToStep("authStepPhone");
  setTimeout(() => {
    const phoneInput = document.getElementById("authPhoneInput");
    if (phoneInput) phoneInput.focus();
  }, 300);
}

function closeAuthModal() {
  const modal = document.getElementById("authModal");
  if (!modal) return;
  modal.classList.remove("auth-modal--open");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("auth-modal-open");
  if (authTimerInterval) { clearInterval(authTimerInterval); authTimerInterval = null; }
}

function authGoToStep(stepId) {
  document.querySelectorAll(".auth-step").forEach(el => el.classList.remove("auth-step--active"));
  const step = document.getElementById(stepId);
  if (step) step.classList.add("auth-step--active");
}

function authShowError(elId, msg) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = msg;
  el.style.display = msg ? "block" : "none";
}

function authStartResendTimer() {
  authResendSeconds = 30;
  const resendBtn = document.querySelector('.auth-modal__link[onclick="authResendCode()"]');
  if (!resendBtn) return;
  if (authTimerInterval) clearInterval(authTimerInterval);
  authTimerInterval = setInterval(() => {
    if (authResendSeconds > 0) {
      authResendSeconds--;
      resendBtn.textContent = `Resend in ${authResendSeconds}s`;
      resendBtn.style.opacity = "0.5";
      resendBtn.style.pointerEvents = "none";
    } else {
      clearInterval(authTimerInterval);
      authTimerInterval = null;
      resendBtn.textContent = "Resend code";
      resendBtn.style.opacity = "";
      resendBtn.style.pointerEvents = "";
    }
  }, 1000);
}

async function authSendCode() {
  const phoneInput = document.getElementById("authPhoneInput");
  const phone = phoneInput.value.trim();
  authShowError("authPhoneError", "");

  if (!phone || !phone.startsWith("+")) {
    authShowError("authPhoneError", "Please enter your phone number with the country code, e.g. +919876543210.");
    return;
  }
  if (phone.length < 8 || phone.length > 16) {
    authShowError("authPhoneError", "That number doesn't look right. Please check it.");
    return;
  }

  const btn = document.getElementById("authSendCodeBtn");
  btn.disabled = true;
  btn.textContent = "Sending...";

  try {
    const res = await fetch("/auth/send-otp", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": window.getCsrfToken() },
      body: JSON.stringify({ phone }),
    });
    const data = await res.json();

    if (!res.ok) {
      authShowError("authPhoneError", data.error || "Could not send code. Please try again.");
      return;
    }

    authPendingPhone = phone;
    document.getElementById("authPhoneDisplay").textContent = phone;
    authGoToStep("authStepCode");

    // Dev mode: show the code in the UI
    if (data.dev_code) {
      const hint = document.getElementById("authDevHint");
      hint.innerHTML = `<span>Dev mode code: <strong>${data.dev_code}</strong></span>`;
      hint.style.display = "block";
    }

    // Auto-fill OTP inputs if dev code is present (convenience)
    if (data.dev_code) {
      const inputs = document.querySelectorAll("#authOtpInputs input");
      data.dev_code.split("").forEach((d, i) => {
        if (inputs[i]) { inputs[i].value = d; }
      });
      if (inputs[5]) inputs[5].focus();
    }

    authStartResendTimer();
    setTimeout(() => {
      const firstInput = document.querySelector('#authOtpInputs input[data-idx="0"]');
      if (firstInput) firstInput.focus();
    }, 300);
  } catch (err) {
    authShowError("authPhoneError", "Network error. Please try again.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Send Verification Code";
  }
}

async function authResendCode() {
  if (!authPendingPhone) return;
  if (authResendSeconds > 0) return;
  authShowError("authCodeError", "");
  const hint = document.getElementById("authDevHint");
  hint.style.display = "none";

  try {
    const res = await fetch("/auth/send-otp", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": window.getCsrfToken() },
      body: JSON.stringify({ phone: authPendingPhone }),
    });
    const data = await res.json();
    if (!res.ok) {
      authShowError("authCodeError", data.error || "Could not resend code.");
      return;
    }
    if (data.dev_code) {
      hint.innerHTML = `<span>Dev mode code: <strong>${data.dev_code}</strong></span>`;
      hint.style.display = "block";
      const inputs = document.querySelectorAll("#authOtpInputs input");
      inputs.forEach(i => i.value = "");
      data.dev_code.split("").forEach((d, i) => { if (inputs[i]) inputs[i].value = d; });
    }
    authStartResendTimer();
  } catch (err) {
    authShowError("authCodeError", "Network error. Please try again.");
  }
}

function authBackToPhone() {
  authGoToStep("authStepPhone");
  authShowError("authCodeError", "");
  authShowError("authPhoneError", "");
  document.getElementById("authDevHint").style.display = "none";
  if (authTimerInterval) { clearInterval(authTimerInterval); authTimerInterval = null; }
  const inputs = document.querySelectorAll("#authOtpInputs input");
  inputs.forEach(i => i.value = "");
}

async function authVerifyCode() {
  const inputs = document.querySelectorAll("#authOtpInputs input");
  const code = Array.from(inputs).map(i => i.value).join("");
  authShowError("authCodeError", "");

  if (code.length !== 6 || !/^\d{6}$/.test(code)) {
    authShowError("authCodeError", "Please enter all 6 digits.");
    return;
  }

  const btn = document.getElementById("authVerifyBtn");
  btn.disabled = true;
  btn.textContent = "Verifying...";

  try {
    // First, verify the OTP
    const verifyRes = await fetch("/auth/verify-otp", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": window.getCsrfToken() },
      body: JSON.stringify({ phone: authPendingPhone, code }),
    });
    const verifyData = await verifyRes.json();

    if (!verifyRes.ok) {
      authShowError("authCodeError", verifyData.error || "That code didn't match. Please try again.");
      // Shake the OTP inputs
      const otpContainer = document.getElementById("authOtpInputs");
      otpContainer.classList.add("auth-otp-shake");
      setTimeout(() => otpContainer.classList.remove("auth-otp-shake"), 400);
      return;
    }

    // If the server returned a name, the user already exists — go straight to success
    if (verifyData.name) {
      authShowSuccess(verifyData.name, verifyData.phone, verifyData.email);
    } else {
      // New user — ask for name/email
      authGoToStep("authStepDetails");
      setTimeout(() => {
        const nameInput = document.getElementById("authNameInput");
        if (nameInput) nameInput.focus();
      }, 300);
    }
  } catch (err) {
    authShowError("authCodeError", "Network error. Please try again.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Verify & Continue";
  }
}

async function authFinish() {
  const name = document.getElementById("authNameInput").value.trim();
  const email = document.getElementById("authEmailInput").value.trim();
  authShowError("authDetailsError", "");

  if (!name) {
    authShowError("authDetailsError", "Please enter your name so we can personalize your experience.");
    return;
  }

  const btn = document.getElementById("authFinishBtn");
  btn.disabled = true;
  btn.textContent = "Creating your account...";

  try {
    // We need to verify OTP again with name/email — but the OTP is already used.
    // Instead, we update the customer profile directly. The verify-otp endpoint
    // already created the customer; we need a separate profile update endpoint.
    // For simplicity, we'll call verify-otp again with the name/email — but the
    // OTP is consumed. So let's add the name/email to the initial verify call.
    // Actually, the cleaner approach: the verify endpoint already created the
    // account. We just need to update the name. Let's use a profile update endpoint.
    const res = await fetch("/auth/update-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": window.getCsrfToken() },
      body: JSON.stringify({ name, email }),
    });
    const data = await res.json();

    if (!res.ok) {
      authShowError("authDetailsError", data.error || "Something went wrong. Please try again.");
      return;
    }

    authShowSuccess(data.name || name, data.phone || authPendingPhone, data.email || email);
  } catch (err) {
    authShowError("authDetailsError", "Network error. Please try again.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Complete Sign Up";
  }
}

function authShowSuccess(name, phone, email) {
  authGoToStep("authStepSuccess");
  const nameEl = document.getElementById("authSuccessName");
  nameEl.textContent = name ? `Hey, ${name}!` : "Welcome!";

  // Update nav and checkout forms
  document.querySelectorAll("[data-auth-signed-out]").forEach(el => el.style.display = "none");
  document.querySelectorAll("[data-auth-signed-in]").forEach(el => {
    el.style.display = "";
    el.textContent = name || phone || "Account";
  });

  // Prefill checkout forms
  const nameField = document.getElementById("buyerName") || document.getElementById("cartBuyerName");
  const emailField = document.getElementById("buyerEmail") || document.getElementById("cartBuyerEmail");
  const phoneField = document.getElementById("buyerPhone") || document.getElementById("cartBuyerPhone");
  if (nameField && name && !nameField.value) nameField.value = name;
  if (emailField && email && !emailField.value) emailField.value = email;
  if (phoneField && phone && !phoneField.value) phoneField.value = phone;

  // Show personalized greeting on homepage
  const greeting = document.getElementById("userGreeting");
  if (greeting) {
    greeting.querySelector(".greeting__name").textContent = name || "there";
    greeting.classList.add("greeting--visible");
  }

  if (window.showToast) window.showToast(`Signed in as ${name || phone}`, "success");

  // Auto-close after showing success
  setTimeout(() => {
    closeAuthModal();
  }, 2000);
}

// ---------- OTP input behavior: auto-advance, paste, backspace ----------
document.addEventListener("DOMContentLoaded", function() {
  const inputs = document.querySelectorAll("#authOtpInputs input");
  if (!inputs.length) return;

  inputs.forEach((input, idx) => {
    input.addEventListener("input", function(e) {
      // Only allow digits
      this.value = this.value.replace(/\D/g, "");
      if (this.value && idx < inputs.length - 1) {
        inputs[idx + 1].focus();
      }
      // Auto-verify when all 6 digits are entered
      const code = Array.from(inputs).map(i => i.value).join("");
      if (code.length === 6) {
        setTimeout(() => authVerifyCode(), 200);
      }
    });

    input.addEventListener("keydown", function(e) {
      if (e.key === "Backspace" && !this.value && idx > 0) {
        inputs[idx - 1].focus();
        inputs[idx - 1].value = "";
      }
      if (e.key === "ArrowLeft" && idx > 0) inputs[idx - 1].focus();
      if (e.key === "ArrowRight" && idx < inputs.length - 1) inputs[idx + 1].focus();
    });

    input.addEventListener("paste", function(e) {
      e.preventDefault();
      const pasted = (e.clipboardData || window.clipboardData).getData("text").replace(/\D/g, "");
      pasted.split("").forEach((d, i) => {
        if (inputs[i]) inputs[i].value = d;
      });
      const code = Array.from(inputs).map(i => i.value).join("");
      if (code.length === 6) {
        setTimeout(() => authVerifyCode(), 200);
      } else if (inputs[Math.min(pasted.length, 5)]) {
        inputs[Math.min(pasted.length, 5)].focus();
      }
    });
  });

  // Close modal on Escape
  document.addEventListener("keydown", function(e) {
    if (e.key === "Escape") closeAuthModal();
  });
});
