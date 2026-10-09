# Personal Sector Swing Radar

Continues PR #14 / feature/early-swing-radar-20261009 from 3191b6b, preserving concurrent 5f5159e INTC paper lab. Do not merge main automatically.

## Access configuration (required before use)
Set server-only Vercel Preview environment variables `SWING_RADAR_USER` and `SWING_RADAR_PASSWORD_SHA256` (SHA-256 hex of a strong personal password), then redeploy the branch. Never put a password or API key in GitHub, HTML or chat. HTTP Basic browser authentication runs over HTTPS. With no configuration all radar pages and radar API modes return 503; invalid credentials return 401. Direct function URLs are also checked. Existing unrelated site endpoints remain unchanged.

The three radar HTML URLs and INTC paper lab are rewritten to the authenticated page function. `includeFiles` packages the HTML. Verify these rewrites on Vercel before production. Authentication identity is whoever knows the configured secret; not verified ChatGPT-account identity. No personal positions are stored server-side. Browser may retain Basic credentials until closed.

## Rules and limitations
- ENTRY requires completed daily SMA50(t)>SMA50(t-5), recent 20MA touch/2% proximity, improving daily EVP, held lows and completed 30m crossover with recent HMA/EVP slope reversal after pullback.
- READY is a close, rising HMA with recent HMA turn and improving EVP after eligible daily pullback. WATCH is eligible daily pullback. WEAK is declining SMA50 or expanding negative daily EVP. EXTENDED is >3% above Hull20. NO_SETUP is valid data without one of these patterns. ERROR/UNQUERIED are separate data states.
- HMA20 is WMA(2*WMA(close,10)-WMA(close,20),4). Integer sqrt convention 4, RTH-only adjusted data; identical TOS values have not been established.
- EVP uses candle close location weighted by volume, trailing previous-20-bar volume normalization and EMA5. Not actual bid/ask delta or TOS Smooth.
- Cutoff is wall-clock minus 15 minutes. 5m coverage checked against XNYS sessions, including holidays and early closes. A missing eligible aggregate may mean illiquidity; it is conservatively an ERROR rather than NO_SETUP. No gap filling.
- One ticker per scan request, two concurrent browser requests; daily+intraday fetch in parallel, connect/read timeouts 2s/8s. 100 tickers => 200 provider requests; entitlement/rate limits still require live verification. No background scan when browser is closed.
- News: connected Finnhub company articles and SEC filings, source links only. Earnings schedule/sector news are explicitly unconfirmed; automatic catalyst/financing-risk analysis is not complete.
- Walls reuse existing cached options endpoint, on selected chart only, with independent timeout. `generated_at` is calculation timestamp, not proof all options quotes share that timestamp.
- Stops use five selected-timeframe lows less 0.5%; this is a structural reference, not a validated swing low algorithm. Risk inputs are local and based on last completed delayed close, not live fills. No orders.

## Required release gates
Preview env setup, authenticated live Massive/Wall/news checks, 100-symbol measured duration and rate limits, TOS parity comparison, stale/holiday checks, real mobile authentication and rewrite behavior. Production release is not authorized until user approval.

## Local verification (this revision)
- PASS: 13 unittest cases (HMA warmup/flat/trend, EVP midpoint, SMA50 five-session comparison, daily gate, data shortage, complete/provisional/gapped 30m, XNYS half-day/holiday, fail-closed/valid/invalid auth).
- PASS: real TOP100 page script under Node DOM fixtures: 100 retained after scanning with one data failure; ENTRY prioritization; search/status filters; embedded 30m chart and return.
- PASS: Python compile/import; all swing HTML inline JS node --check; git diff --check.
- NOT VERIFIED: live provider values and duration, mobile rendering, authenticated Vercel access. Local Chromium download failed (invalid/truncated archive). No claim of browser QA based on DOM fixtures.
