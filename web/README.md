# offpeak-site

One static page. Five surfaces behind hash routes: `#/` desk · `#/calculator` savings ·
`#/board` spread board (with the carbon leg) · `#/prices` prices, marks and queue ·
`#/receipts` receipts.

No build step, no dependencies, no framework. `index.html` is the whole site —
all CSS and JS inline, one webfont request to Google Fonts.

## Deploy to Vercel

The site source lives in this repo under `web/`, and the Vercel project's **root
directory is `web/`**. Once the Git integration is connected, every push to the
production branch deploys on its own; nothing to run.

Deploying by hand still works, from inside `web/`:

    cd web
    npx vercel        # preview
    npx vercel --prod # production

Framework preset: **Other**. Build command: none. Output directory: `.` (relative to
`web/`). `vercel.json` beside this file carries the headers and URL rules.

## Deploy anywhere else

Any static host works — copy `index.html` to the web root. GitHub Pages, Cloudflare
Pages, S3, Netlify. Hash routing means no server rewrites are needed.

## Custom domain

Once `offpeak.ai` is bought: add it in Vercel → Project → Domains, point the apex
A record / CNAME as instructed. The docs site (`offpeak-ai.github.io/offpeak`) stays
where it is and is linked from the nav and the footer.

## Where the numbers come from

Nothing on the page is invented. Editing any of these means editing the source data too:

| Figure | Source |
|---|---|
| Token prices | `offpeak.prices`, sheet snapshot 2026-08-30 (`PRICES` in the inline script; `SHEET_DATE` stamps every citation). A newer `sheet/latest.json` on board-data replaces it on load |
| Lane per row | `l: "clock"` on the DeepSeek rows mirrors `prices.lane_for()`; every place the page prints "batch" beside a row reads it and says "off-peak" instead |
| Batch discount | 50% of standard, published on every batch venue; DeepSeek's off-peak clock rate is the same half of peak |
| Fast → batch 4x | `urgency_spread()` on the gpt-5.6-sol sheet ($8/$40 vs $2/$10) |
| GB carbon + power | board-data branch, `nightly/*-mark.json` (NESO + Octopus Agile) |
| CAISO / ERCOT power | board-data branch (gridstatus DA LMP) |
| US carbon | EIA-930; publishes ~1 day behind, recorded unavailable-and-documented |
| Batch turnaround | `nightly/<session>-queue.json` as committed (`PV_QUEUE`, the floor); the hourly probe now runs privately and publishes back `nightly/QUEUE.md` and `nightly/queue-summary.json`, which the page renders as a per-venue summary table when present |
| Venue coverage | the coverage table on `#/prices` — hand-maintained, dated by the sheet date |
| 0.24 Wh / request | Google, Aug 2025, full-stack median Gemini Apps text prompt |

The carbon sessions live in `SESSIONS` in the inline script: `{ d, kind, naive, chosen }`,
`kind` being `mark` or `quote`. Three are hand-entered (the ones the prose discusses);
every later session is read on load from `nightly/<date>-mark.json` — `carbon_gb.cleanest_5h`
/ `dirtiest_5h` for the chosen window, the two named windows for the naive one — and
the session count in the caption is computed, not typed.

The public/private rule for queue data: the site may show per-venue percentiles and
counts, never hour-of-day, never per-row hourly data.

## Copy rules baked into this page

1. The deadline is the primitive — never a day-part. No "overnight", "by morning".
2. No timezone-bound language — "session", "daily", "inside 24 hours", or a UTC stamp.
3. Discounts read as savings ("50% off"), signed percentages only for measured deltas.
4. Day-part words survive only as literal facts (a UTC stamp, a named 00:00-05:00 window,
   a physical mechanism, a venue product name, a filename).
5. Fastest-to-batch is **4x**. The old 8x reading was an extraction artifact and is retracted.
