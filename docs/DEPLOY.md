# Deploying VitaDrive (Vercel + Supabase + Clerk)

| Piece | Service | What it does |
|---|---|---|
| App hosting | **Vercel** | Runs the FastAPI backend as serverless functions and serves the dashboard over HTTPS. |
| Database | **Supabase** | Managed PostgreSQL that stores vehicles, readings, alerts and services. |
| Login | **Clerk** | Sign-up / sign-in screens, Google etc. login, sessions. Each user only sees their own vehicles. |

You need accounts on all three (each has a free tier). About 15 minutes.

## 1. Supabase (database)

1. Go to https://supabase.com/dashboard, click **New project**, choose a name and a strong database password, and pick a region close to your users.
2. When the project is ready, click **Connect** at the top of the project page.
3. Copy the **Transaction pooler** connection string (host `...pooler.supabase.com`, port **6543**). Replace `[YOUR-PASSWORD]` with your database password.
   Serverless functions open many short connections, and the pooler handles that.

### Database migrations

VitaDrive creates and updates database tables automatically with Alembic when it starts. If you already
have a database from an older VitaDrive version, it is detected and marked as version 0001 automatically;
then it is upgraded to the latest version. There is nothing you need to click or change. To run an upgrade yourself, use
`DATABASE_URL=... vitadrive db upgrade`. To manage migrations manually instead, set
`VITADRIVE_AUTO_MIGRATE=0`.

### Abuse protection

Requests are limited per user or vehicle to help prevent accidental or abusive traffic. The counters are
stored in the database, so limits are shared across Vercel serverless instances.

| Action | Limit |
|---|---:|
| Add demo vehicles | 5 per hour per user |
| Ask VitaDrive | 10 per minute and 50 per UTC day per user |
| Send vehicle readings | 120 per minute per VIN |
| Search for nearby shops | 20 per minute per user |
| Create an API key | 10 per hour per user |

To change the daily Ask quota, open your Vercel project and go to **Settings → Environment Variables**,
add `ASK_DAILY_QUOTA` with the number of questions allowed per user each UTC day, then **Redeploy**.
For local development only, set `VITADRIVE_RATE_LIMITS=0` to turn off all request limits.

### Automatic clean-up of old readings (optional)

VitaDrive keeps recent readings in full. Once readings are more than 30 days old, they are compacted into
one daily summary, so the database stays smaller while maintenance forecasts and reports can still use
the history. A scheduled job can do this clean-up for you; without its secret, the job stays off.

1. Generate a secret on your computer with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"` and copy the result.
2. In Vercel, open your project and go to **Settings → Environment Variables**. Add `CRON_SECRET` with
   the generated value, then click **Redeploy**.
3. To confirm it ran, go to **Vercel → Project → Settings → Cron Jobs** and check the job history.

You can also run the clean-up yourself when needed:
`DATABASE_URL=... vitadrive prune`. The summaries are included with recent readings, so maintenance
forecasts are unaffected after old raw rows are compacted.

## Logs and monitoring

Application logs are JSON lines in **Vercel → Project → Logs**. Every response includes an `X-Request-ID`;
search for that ID to find the matching access log. Use `/api/health` for uptime checks.

To send errors to Sentry:

1. Go to sentry.io, select the free **Developer** plan, click **Create Project**, and choose **FastAPI**.
2. Copy the project's DSN.
3. In Vercel, open **Project → Settings → Environment Variables**, add `SENTRY_DSN` with that value,
   then **Redeploy**.

`SENTRY_TRACES_SAMPLE_RATE` optionally controls performance tracing and defaults to `0` (off).
Set `LOG_LEVEL` to change application log verbosity; it defaults to `INFO`.

## 2. Clerk (login)

1. Go to https://dashboard.clerk.com, click **Create application**, and enable the sign-in methods you want (email, Google, ...).
2. Open **Configure → API keys** and copy the **Publishable key** (`pk_test_...` or `pk_live_...`).
   VitaDrive only needs the publishable key. The backend checks sessions with Clerk's public signing keys, so no secret key is stored.

## 3. Vercel (hosting)

1. Go to https://vercel.com/new and import the `adotak/vitadrive` GitHub repo. Set **Framework Preset** to *FastAPI* (usually auto-detected) and leave the build settings at their defaults. Vercel runs the `app` object in the root `app.py`.
2. Under **Environment Variables**, add:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Supabase transaction-pooler string from step 1 |
   | `CLERK_PUBLISHABLE_KEY` | Clerk publishable key from step 2 |
   | `CLERK_AUTHORIZED_PARTIES` | *(recommended)* your site URL, e.g. `https://vitadrive.vercel.app` |

3. Click **Deploy**. Then open the URL, sign in, and click **Add demo cars** to check that everything works.

Each push to `main` redeploys automatically, and pull requests get preview URLs.

### Going to production with Clerk

Development instances (`pk_test_`) show a "Development mode" badge. When you're ready, create a **production instance** in Clerk. It requires your own domain: add the domain in Vercel and follow the DNS records Clerk asks for. Then replace `CLERK_PUBLISHABLE_KEY` with the `pk_live_` key and redeploy.

### Turning on "Ask VitaDrive" (optional AI chat)

The chat uses OpenAI through LangChain. Without a key the chat box is hidden and everything else works.

1. Go to https://platform.openai.com/api-keys, sign in, and click **Create new secret key**. Copy it (it starts with `sk-`).
2. Add a payment method under **Settings → Billing**. OpenAI charges per question; with the default model each question costs a fraction of a cent.
3. In Vercel, open **Settings → Environment Variables** and add `OPENAI_API_KEY` with that key. Optionally add `OPENAI_MODEL` (default `gpt-4o-mini`).
4. Go to **Deployments**, open the **⋯** menu on the latest deployment and click **Redeploy**.

Keep the key only in Vercel. Never commit it or paste it into chats.

### Turning on phone alerts (optional)

VitaDrive can send a notification to your phone or computer when a reading turns critical, even when the site is closed. It uses the browser's built-in Web Push, which is free. The server needs a pair of keys (called VAPID keys) to sign its messages.

1. Generate the keys once, on your own computer: `pip install -e .` and then `vitadrive vapid-keys`. It prints two lines.
2. In Vercel, open **Settings → Environment Variables** and add both: `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`. Optionally add `VAPID_SUBJECT` (a `mailto:` address push services can contact; default `mailto:alerts@vitadrive.app`).
3. Go to **Deployments**, open the **⋯** menu on the latest deployment and click **Redeploy**.
4. Open the dashboard, click **Turn on alerts** and allow notifications. You get a test notification straight away. Do this on each device you want alerts on.

On iPhone and iPad, open the site in Safari, tap **Share → Add to Home Screen**, then open VitaDrive from the home screen and turn alerts on there (Apple only allows web notifications for installed apps).

Keep the private key only in Vercel. If you change the keys later, every device has to turn alerts on again.

The AI fault detector needs no setup: its trained model is part of the code.

## 4. Connecting a car

1. In the dashboard, register the vehicle (or pick it) and click **API key**. Copy the key, because it's shown only once.
2. Have the car's telematics gateway, phone app or OBD-II device POST readings:

```bash
curl -X POST https://<your-app>.vercel.app/api/vehicles/<VIN>/readings \
  -H "X-API-Key: vd_..." -H "content-type: application/json" \
  -d '{"vin":"<VIN>","odometer_km":42010,"engine_oil_life_pct":12,"tire_pressure_fl_kpa":195}'
```

The `vitadrive obd` command, which streams from an ELM327 dongle, has to run on a device connected to the car, such as a laptop or Raspberry Pi. Vercel functions are short-lived and can't keep a connection to the car open.

## Running locally against Supabase

```bash
cp .env.example .env   # fill in the values
export $(grep -v '^#' .env | xargs)
vitadrive serve        # http://127.0.0.1:8000 with Clerk login and the Supabase database
```

If `CLERK_PUBLISHABLE_KEY` is unset, login is disabled and everything belongs to one local user. Use that only on your own machine.

## Costs and limits to know

- Each free tier has usage limits; check the current pricing pages.
- Supabase pauses free projects after a period of inactivity. Unpause them from the dashboard, or upgrade.
- OpenAI bills each chat question to your OpenAI account (only if `OPENAI_API_KEY` is set).
- Vercel function duration is capped by plan; the default covers **Add demo cars**.
