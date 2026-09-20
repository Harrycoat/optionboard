# GEXoption.com development status

## Completed

- Personal watchlist cards on the home page (maximum 20)
- Search result button to add/remove a ticker
- 15-minute delayed-data notice
- Price, daily change, Put Wall, Call Wall, and simple zone status
- Browser storage fallback for visitors
- Supabase email sign-up/login UI
- Signed-in watchlist synchronization across devices
- Row Level Security schema for private per-user watchlists
- AI Industry Leader Map with Phase 1/2/3 sector filters
- CNN Fear & Greed market-climate proxy endpoint
- One-line Weinstein Stage 1-4 + Put Wall/current price/Call Wall view
- Automatic response label: observe, entry review, hold, hedge review, or breakout check

## Activation steps

1. Create a Supabase project.
2. Open **SQL Editor** and run `supabase/schema.sql`.
3. In Vercel, add `SUPABASE_URL` and `SUPABASE_ANON_KEY`.
4. Redeploy the project.
5. In Supabase Authentication settings, add `https://gexoption.com` as the Site URL and redirect URL.

Without these variables, the existing site and browser-only watchlist continue to work.

## Next

- Connect the AI Leader Map universe to an admin-managed ticker list
- Add daily Stage/Wall snapshots for historical change comparison
- Add stock volume plus call/put option volume
- Add Korean/English language switch
- Build a ticker detail layout with chart, GEX, option volume, and news
- Mobile layout verification

## Important

Before a public paid launch, confirm that the Massive Individual plans permit displaying derived stock/options data to multiple website users.
