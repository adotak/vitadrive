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

You don't need to create any tables. VitaDrive creates them the first time it starts.

## 2. Clerk (login)

1. Go to https://dashboard.clerk.com, click **Create application**, and enable the sign-in methods you want (email, Google, ...).
2. Open **Configure → API keys** and copy the **Publishable key** (`pk_test_...` or `pk_live_...`).
   VitaDrive only needs the publishable key. The backend checks sessions with Clerk's public signing keys, so no secret key is stored.

## 3. Vercel (hosting)

1. Go to https://vercel.com/new and import the `adotak/vitadrive` GitHub repo. Leave **Framework Preset** as *Other* and the build settings at their defaults.
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
- Vercel function duration is capped by plan. `vercel.json` sets 60 s, which covers **Add demo cars**.
