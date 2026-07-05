function showBuyError(msg) {
  const el = document.getElementById("buyError");
  el.textContent = msg;
  el.style.display = "block";
}

async function startCheckout(productId, productName) {
  const name = document.getElementById("buyerName").value.trim();
  const email = document.getElementById("buyerEmail").value.trim();
  const phone = document.getElementById("buyerPhone").value.trim();
  const btn = document.getElementById("buyBtn");

  document.getElementById("buyError").style.display = "none";

  if (!name || !email) {
    showBuyError("Please enter your name and email to continue.");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Please wait...";

  try {
    const res = await fetch("/api/create-order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: productId, name, email, phone }),
    });
    const data = await res.json();

    if (!res.ok) {
      showBuyError(data.error || "Something went wrong. Please try again.");
      resetBtn(btn, productName);
      return;
    }

    const options = {
      key: data.razorpay_key,
      amount: data.amount,
      currency: data.currency,
      name: document.querySelector(".nav__brand").textContent,
      description: data.product_name,
      order_id: data.razorpay_order_id,
      prefill: {
        name: data.customer_name,
        email: data.customer_email,
        contact: data.customer_phone,
      },
      theme: { color: "#0a0a0a" },
      handler: async function (response) {
        const verifyRes = await fetch("/api/verify-payment", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            razorpay_order_id: response.razorpay_order_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature,
          }),
        });
        const verifyData = await verifyRes.json();
        if (verifyRes.ok) {
          window.location.href = "/track";
        } else {
          showBuyError("We could not confirm your payment. Please contact us with your order reference.");
        }
      },
      modal: {
        ondismiss: function () {
          resetBtn(btn, productName);
        },
      },
    };

    const rzp = new Razorpay(options);
    rzp.on("payment.failed", function () {
      showBuyError("Payment failed. Please try again.");
      resetBtn(btn, productName);
    });
    rzp.open();
    resetBtn(btn, productName);
  } catch (err) {
    showBuyError("Network error. Please try again.");
    resetBtn(btn, productName);
  }
}

function resetBtn(btn, productName) {
  btn.disabled = false;
  btn.textContent = btn.dataset.originalText || btn.textContent;
}

document.addEventListener("DOMContentLoaded", function () {
  const btn = document.getElementById("buyBtn");
  if (btn) btn.dataset.originalText = btn.textContent;
});
