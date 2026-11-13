# Valiwatt Live: the live forecasts (plan)

## The owner's ruling, 2026-11-13
His go on this plan, as carded:

1. **Official:** v2 refitted on data through 2025, v2's rules unchanged.
2. **Experimental:** the same refit, corrected for the 1.2–1.7 % low bias, with the asymmetric range that varies with
   forecast temperature. Its rules are frozen before any 2026 data is looked at.
3. Both forecasts go live by early December and are logged before the demand comes in, so this winter decides between
   them. No new model research for now.

The live record is new and separate: its own freeze tag, its own log. It is not a departure from `rules-frozen-v2`,
and it changes nothing in v2's 2024 and 2025 records.

## Why this shape
- Every year 2020–2025 has been seen, so no held-out year remains for a changed rule. A forecast issued before its
  demand exists is the only clean test left, and the live page produces one every day.
- The point forecast's form stays: v3's 2025 test did not show its terms better than v2's.
- The evidence for the experimental changes is the interval-shape measurement of 2026-11-12 (scratch, outside the
  tracked runs): the asymmetric band by forecast-temperature bin, rule (c), scored −518 MW [−684, −355] on the interval
  score against the symmetric band, pooled 2023–2024 (95 % week-block CI), and won in every year 2021–2024; the median
  error was +1.2 to +1.7 % in every year 2021–2025.
- The two bands differ most below −11 °C, so this winter is the test; missing it costs a year.

## Conditions
- No 2026 data is read before both models are frozen and the tag is pushed. At the ruling, `data/raw` held no 2026
  file.
- Each forecast is written, with its issue time and the hash of its model, before the first hour it forecasts.
- Hydro-Québec's demand is CC BY-NC 4.0. The page stays private (owner's machine, Tailscale) until the owner rules on
  republishing it.

## The experimental rule, pre-declared
The range is rule (c) as the interval-shape measurement scored it: per forecast-temperature bin, the 5 % and 95 %
quantiles of the relative errors e = (actual − forecast)/forecast of the official forecast, applied around the
official forecast. The bias correction is the same bins' median of e: experimental point = official × (1 + median_bin).
So the corrected point is the centre the range already implies, bin by bin. One global factor is not used: in 2024 the
band centred near +3 % below −11 °C and near +1.3 % above 10 °C, so one factor would under-correct in the cold.
- Bins on the day's mean forecast temperature: <−11, −11..0, 0..10, >10 °C; a bin with fewer than 200 calibration hours
  merges into its warmer neighbour.
- Window: the 12 monthly folds before the live start (the folds of 2025).
- What was not measured: the corrected point's error. Before the freeze, the exact rule is scored once on the 2024–2025
  folds through the code path that issues live; if its interval score or its error departs materially from rule (c)'s
  measurement, the owner hears it before the tag.
- The page's range text (e.g. "−3,5 % à +9,7 %") is relative to the official forecast.

## Open specification (to settle before the freeze)
- **Calibration of the refit.** v2's freeze takes its half-width from the 12 validation folds before the replayed
  year, and validation covers 2020–2023 only. The 2026 freeze needs walk-forward folds over 2024–2025 under v2's
  rules; their scores calibrate both bands.
- **The live inputs.** The lags need 24 h and 168 h of demand, and no 2026 export exists. To check: how far back the
  15-minute feed reaches, whether it measures the same quantity as the yearly export, and the NBM run available at the
  noon origin. Checking these reads the feed's schema and coverage, not its values; a blind, write-only capture may
  start before the freeze, read by nothing until the tag.
- **The daily issue:** the newest compliant NBM run at the noon origin, as in v2's replay clock.
- **Proof of order:** each forecast's log line carries its issue time and its sha256, and is committed (or otherwise
  timestamped outside the machine) before the first hour it forecasts.

## Before the tag
- Rehearse issue → log → score on a seen day of 2025.
- A fresh-context review of this section and the freeze code.
- New code lives in its own package and calls `valiwatt.forecast`; v2's modules, `config.FREEZES`, `THROUGH_YEARS` and
  their tests stay unchanged (the v3 report stamp hashes tests).

## Out of scope
The intraday "adjusted" line in prototype A3 is a mock. It is not part of the ruling and does not ship as a forecast.

## Build order
1. Walk-forward folds 2024–2025 and the 2026 freeze (official and experimental), tag `rules-frozen-live`.
2. The demand feed (every 15 min) and the daily issue at 12 h, written as static JSON plus an append-only log.
3. The page from prototypes A3/A4, reading the JSON; FR by default, EN switch.
4. The Wallpaper Engine skin on the same files.
