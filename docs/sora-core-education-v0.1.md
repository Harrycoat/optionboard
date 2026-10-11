# Sora core education criteria — review draft v0.1
Source: '소라 핵심 교육 기준 초안 v0.1.docx' (DOT, 2026-10-10 PDT).
Status: DRAFT FOR USER REVIEW; NOT an approved trading signal. Supersedes neither the existing introductory lesson nor live trading rules.

## Separation of lessons
- Introductory lesson **2026-10-10.v1**: 멈춤 → 다음 봉 저점 유지 → 재상승. Retain as beginner lesson.
- **Main draft v0.1**: candidate selection → entry → hedge → portfolio P/L → review. Treat as reference, not a fully specified executable strategy.

## Candidate selection (daily chart)
- Relative strength *around* 90; indicator, lookback and acceptable range not yet defined. Do not rewrite as RSI>=90 or RS>=90.
- Price above an upward-sloping **daily 50-day moving average**, preceded by a rise and subsequent pullback. MA type and slope rule unconfirmed.
- Observe decreasing sell pressure, Smooth upward turn, Hull21 **band** reentry. Distinguish Hull21 band from a Hull20 line.

## Stock entry (swing 30m / short-term 5m)
Sequence: Hull21 **band** reentry, then Put Wall support AND buying pressure strictly >50 AND Smooth upward turn. All four components must be individually assessed as MET, UNMET or UNAVAILABLE; do not guess missing values. The historical 90 buying-pressure scalping threshold is unrelated.

## Long-stock protective put
At Call Wall resistance, seek buying pressure strictly <50 and Smooth downward turn; pattern on selected timeframe:
1. Last bullish candle stalls (stall definition unresolved)
2. Bearish candle engulfs the bullish candle (body vs full range unresolved)
3. Subsequent bearish candle enters Hull21 band from above (band math unresolved)
Then discuss whether a protective put purchase should be considered, without executing a trade.
Calculate total position P/L only with verified prices, quantities and option data:
stock P/L + [put's current/sale value − purchase premium] × contract multiplier × number of contracts − transaction costs, adjusted for other cash flows, as appropriate. No fabricated put strike, expiration, delta, or performance.

## Teaching and bilingual invariants
- Explain why a candidate qualifies, what is met, unmet, and unavailable, and what invalidates the scenario.
- Default teaching: swing = **30m**, short-term = **5m**. The daily 50MA is always daily.
- English and Korean must express exactly the same numeric comparisons: >50 is NOT >=50, and <50 is NOT <=50.
- Data provenance, bar start time, timezone, stale/delayed status and rule version must be visible.
- If only OHLCV and Hull20/estimated EVP are available, DO NOT claim verified Hull21 band, TOS Smooth, buying pressure, RS or option walls.
- Avoid forward-looking leakage; replay and future-masked training remains only a proposal.

## Open questions before promoting from draft
1. RS vs RSI and comparison universe/time period and around-90 threshold.
2. Daily 50MA type and rising-slope lookback.
3. Buying-pressure definition/source and precise equality behavior at 50.
4. TOS Smooth formula and downturn confirmation.
5. Hull21 band formula, boundaries and wick/body interaction.
6. Completed vs partial candles on 30m and 5m.
7. Put/Call Wall source, support/resistance tests, price/time tolerance.
8. Exact stall/engulf pattern; body versus full-wick range.
9. Equity entry invalidation, stop and exit.
10. Protective put liquidation or rolling rules.
11. Put strike, maturity, quantity and max premium budget.
12. How conflicting 30m and 5m signals are reconciled.
13. Whether candle-by-candle replay with hidden future data is approved.

## Handoff / revalidation (2026-10-10)
Repo Harrycoat/optionboard; branch feature/sora-context-lesson-20261010.
Deployment and Gemini live interaction are NOT verified by this document.
- Preview route /swing-top100.html uses Vercel rewrite to /api/index?__route=sora_page; requires existing protected access.
- Chat POST /api/sora_chat checks private access, fetches completed market bars server-side, then calls Gemini with server-only SORA_GEMINI_API_KEY.
- Run tests with AMZN in Korean and English, 30m→5m→30m, change ticker and rapid switches; validate every answer's ticker, timeframe, source, bar UTC, lesson version.
- Verify missing indicators are UNAVAILABLE, not negative or magically MET; verify 401/403/429 and timeout paths in controlled test.
- Do not log keys or publish operating data; do not merge/deploy production without explicit approval.
- Preview URLs and provider secrets must be checked separately; a Vercel 'success' build is not execution evidence.
