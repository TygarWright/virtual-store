# Your Store — Virtual Products Website

A lean, premium, black-and-white storefront for selling virtual/digital products in India,
with Razorpay checkout and a simple admin panel built for a non-technical owner.

**Tech stack (deliberately minimal):** Flask (Python) + SQLite (a single file, no database
server to manage) + vanilla HTML/CSS/JS. No build step, no frontend framework, no ORM.

---

## 1. What this website does

- **Storefront**: premium, minimal, editorial black/white design. Fast — no heavy JS
  frameworks, no big images.
- **Products**: sell any number of virtual products, each with photos, price, and description.
- **Checkout**: Razorpay handles the actual payment (cards, UPI, netbanking, wallets).
- **Delivery**: after payment, the order goes into "Awaiting Delivery." You (the admin)
  manually review each order and send the customer their file/link/code — good for keeping
  quality control on what goes out.
- **Order tracking**: customers can check their order status any time using their Order
  Reference + email — no customer accounts needed.
- **Admin Panel**: edit every piece of text on the site, add/remove homepage sections,
  manage products and images, and manage orders — all through simple forms, no code.

---

## 2. Running it on your own computer (to try it out)

You'll need Python 3.10+ installed.

```bash
cd virtual-store
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# open .env in any text editor and fill in your details (see section 4 below)

python app.py
```

Then open **http://localhost:5000** in your browser. The admin panel is at
**http://localhost:5000/admin/login**.

The first time it runs, the site creates its own database file at `instance/store.db`
and a default admin login using whatever `ADMIN_USERNAME` / `ADMIN_PASSWORD` you put in `.env`.
**Log in and change the password immediately** (My Account → Change Password).

---

## 3. Putting it live on the internet

This app runs anywhere that can run a Python app. The easiest beginner-friendly options:

- **Render.com** or **Railway.app** — connect your code, they give you a live URL and handle
  HTTPS automatically. Free/cheap tiers available. Set your `.env` values as "Environment
  Variables" in their dashboard instead of a `.env` file.
- Start command they'll need: `gunicorn app:app`

A technical friend or freelancer can have this live in under 30 minutes if you'd rather not
do the hosting step yourself — everything they need is already in this folder.

**Important:** the SQLite database (`instance/store.db`) and uploaded images
(`static/uploads/`) must live on **persistent storage** — on Render this means adding a
"Persistent Disk"; on Railway it's on by default. Ask your host if unsure.

---

## 4. One-time technical setup (needs to happen once, ideally by a developer)

Open `.env` (or your hosting provider's Environment Variables page) and fill in:

| Variable | What it is |
|---|---|
| `SECRET_KEY` | Any long random string — keeps admin logins secure. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | From your Razorpay Dashboard → Settings → API Keys. Needed before customers can pay. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Your first admin login (change the password inside the site afterwards). |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` | Optional — if filled in, the site automatically emails customers when their order is paid and delivered. If left blank, you just message customers yourself using the details on their order page. |

You'll need a **Razorpay account** (https://razorpay.com) — sign up, complete their KYC, and
get your API keys from the Dashboard. Until this is done, the "Buy Now" button will show
customers a friendly "payments not set up yet" message instead of breaking.

---

## 5. Using the Admin Panel (no technical knowledge needed)

Go to **yourwebsite.com/admin/login** and log in.

- **Dashboard** — a quick snapshot: how many orders need delivering, total revenue, recent orders.
- **Orders** — every purchase shows up here. When an order says "Awaiting Delivery," open it,
  type the download link / code / instructions the customer should receive, and click
  **"Mark as Delivered."** That's it — the customer sees it immediately on their tracking page.
- **Products** — click "Add New Product" to create one: name, price, description, and photos.
  Drag in one or more images; they're automatically resized so your site stays fast. Untick
  "Visible on the live site" to hide a product without deleting it.
- **Homepage Sections** — add blocks of text to your homepage (e.g. "Our Story," "Why Us").
  Use the ↑ / ↓ buttons to reorder them, or untick "Show on homepage" to hide one temporarily.
- **Site Settings** — change your site name, tagline, big homepage headline, About Us text,
  contact details, and footer note. Every field has a short note explaining what it controls.
- **My Account** — change your admin password.

Everything saves instantly and appears on your live site the moment you click Save —
there's no "publish" step to remember.

---

## 6. How a sale works, end to end

1. A customer browses your catalogue and clicks "Buy Now" on a product.
2. They enter their name and email and pay via Razorpay (cards/UPI/netbanking/wallets).
3. Once payment succeeds, their order appears in your **Orders → Awaiting Delivery** list.
4. You open the order, write the delivery details, and click "Mark as Delivered."
5. The customer can check progress any time at **yourwebsite.com/track** using their Order
   Reference + email — no login required.

---

## 7. Project structure (for whoever maintains it)

```
app.py               → all routes (storefront + admin)
config.py            → reads settings from .env / environment variables
database.py          → SQLite schema + seed data
razorpay_client.py   → tiny Razorpay Orders API wrapper + signature verification
helpers.py           → auth, image resizing, CSRF, optional email sending
templates/           → Jinja2 HTML templates (storefront + templates/admin/)
static/css/style.css → the entire design system (one file, CSS variables)
static/js/           → checkout flow + (space for any future JS)
static/uploads/      → product images live here
instance/store.db    → the whole database — back this up regularly
```

Backing up your store = copying `instance/store.db` and the `static/uploads/` folder
somewhere safe. That's the entire business's data.

---

## 8. Security notes

- Admin passwords are hashed (never stored in plain text).
- Payments are verified server-side using Razorpay's signature check — the browser
  can't fake a successful payment.
- Change `SECRET_KEY` and the default admin password before going live.
- Keep `.env` private — never share it or upload it anywhere public.
