# offpeak-site

One static page. Five surfaces behind hash routes: `#/` desk · `#/calculator` savings ·
`#/carbon` carbon ledger · `#/board` spread board · `#/receipts` receipts.

No build step, no dependencies, no framework. `index.html` is the whole site —
all CSS and JS inline, one webfont request to Google Fonts.

## Deploy to Vercel

    npx vercel        # preview
    npx vercel --prod # production

Or connect the repo at vercel.com/new and point the project at this folder.
Framework preset: **Other**. Build command: none. Output directory: `.`

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
| Token prices | `offpeak.prices`, sheet snapshot 2026-08-21 (`PRICES` in the inline script) |
| Batch discount | 50% of standard, published on both venues |
| Fast → batch 4x | `urgency_spread()` on the gpt-5.6-sol sheet ($8/$40 vs $2/$10) |
| GB carbon + power | board-data branch, `nightly/*.json` (NESO + Octopus Agile) |
| CAISO / ERCOT power | board-data branch (gridstatus DA LMP) |
| US carbon | EIA-930; publishes ~1 day behind, recorded unavailable-and-documented |
| 0.24 Wh / request | Google, Aug 2025, full-stack median Gemini Apps text prompt |

The carbon session constants live in `SESSIONS` in the inline script. Add a session
by adding a key: `"<spread>": { label, dirty, clean }` in gCO2/kWh.

## Copy rules baked into this page

1. The deadline is the primitive — never a day-part. No "overnight", "by morning".
2. No timezone-bound language — "session", "daily", "inside 24 hours", or a UTC stamp.
3. Discounts read as savings ("50% off"), signed percentages only for measured deltas.
4. Day-part words survive only as literal facts (a UTC stamp, a named 00:00-05:00 window,
   a physical mechanism, a venue product name, a filename).
5. Fastest-to-batch is **4x**. The old 8x reading was an extraction artifact and is retracted.
