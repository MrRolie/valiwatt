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
- Each day's forecast is issued from readings stamped at or before its origin (D 00:00 EST); a reading stamped after
  the origin is never read, even when it has arrived by issue time. It is logged with its issue
  time, the two model hashes and its own hash before the end of D's first hour, the earliest moment a measured value
  of that hour exists (`live/issue.py`, `append_log`, on the machine's clock). The first issue logged for a day is the
  one that counts; a later one is refused, never a replacement. A day not issued by the deadline is missed and stays
  missed.
- Outside proof: each issue's sha256 is committed and pushed to this public repository's `live-hashes` branch before
  the deadline; the hash reveals nothing of the forecast, and GitHub's push record dates it. The forecasts themselves
  stay private with the page.
- Hydro-Québec's demand is CC BY-NC 4.0. The page stays private (owner's machine, Tailscale) until the owner rules on
  republishing it.

## The experimental rule, pre-declared
The range is rule (c) as the interval-shape measurement scored it: per forecast-temperature bin, the 5 % and 95 %
quantiles of the relative errors e = (actual − forecast)/forecast of the official forecast, applied around the
official forecast. The bias correction is the same bins' median of e: experimental point = official × (1 + median_bin).
So the corrected point is the centre the range already implies, bin by bin. One global factor is not used: in 2024 the
band centred near +3 % below −11 °C and near +1.3 % above 10 °C, so one factor would under-correct in the cold.
- Bins on each hour's forecast temperature (the NBM value the model reads): <−20, −20..−11, −11..0, 0..10, >10 °C;
  going coldest first, a bin with fewer than 200 calibration hours merges into its warmer neighbour (a warm leftover
  merges into the group before it). The bounds are the split-conformal quantiles at α/2 on each side.
- Window: the 12 monthly folds before the live start (the folds of 2025).
- Scored 2026-11-13 on the 2024-2025 folds (decision year 2025): range −592 MW [−840, −349] on the interval score vs
  the official; corrected point MAPE −0.15 pp [−0.28, −0.02]; consistent with the measurement, no departure.
  Both ranges covered under 90 % in 2025 (87.7 % official, 86.9 % experimental).
- What was not measured before that: the corrected point's error. Before the freeze, the exact rule is scored once on the 2024–2025
  folds through the code path that issues live; if its interval score or its error departs materially from rule (c)'s
  measurement, the owner hears it before the tag.
- The page's range text (e.g. "−3,5 % à +9,7 %") is relative to the official forecast.

## Open specification (to settle before the freeze)
- **Calibration of the refit (settled):** `live/freeze.py` runs v2's walk-forward on the 12 folds of 2025 and takes
  their rows scored by the 2026 fit clock (`calibration_rows`), as v2's freeze would for a 2026 replay.
- **The live inputs.** The lags need 24 h and 168 h of demand, and no 2026 export exists. Found: the 15-minute feed
  keeps 48 h, so a blind capture runs from 2026-11-13 (`scripts/live-capture.sh`, twice an hour), read by nothing until
  the tag. Settled from the publisher's documentation (2026-11-13): the export's hour is the mean of 5-minute samples,
  labelled at its end (2:00 = 1:05 to 2:00); the feed's value is a reading taken at its stamp. A live hour H is
  therefore the mean of the four readings stamped in (H − 1 h, H], labelled H: 4 samples where the export has 12. The
  difference is measured once the export covers live days; the verdict's record vintage is the export. Still open:
  the NBM run available at the origin.
- **The daily issue:** v2's origin, D 00:00 EST (`features.day_origin`), from demand through D−1's last hour and the
  newest NBM run available by then. The issue runs a few minutes after midnight, once D−1's last hour is in the feed,
  and is logged before D's first hour ends (`live/issue.py`, `append_log`).
- **Consequence for the page:** v2's forecast is not issued at noon the day before (v3's horizon was). The prototype's
  "émise la veille à 12 h", its Flex D card (peak notices go out the afternoon before) and its peak-forecast vintages
  row are corrected in the page build.
- **Proof of order:** see Conditions: the log line and its pushed hash, both before the end of D's first hour. The
  runner records a missed or late day as a "missed" line in the log, with its reason; nothing replaces it.

## The winter's verdict, declared before any outcome
- Period: issues for the days 2026-12-01 to 2027-03-31 (local standard time). Both forecasts come from the same issue,
  so every scored hour is paired.
- Target: the hourly mean of Hydro-Québec's 15-minute feed as captured (the live vintage); the whole comparison is
  scored again on the yearly export once Hydro-Québec publishes 2026–2027, and that export is the vintage of record: the first yearly export covering
  each day, fetched once, its sha256 recorded.
- Scored hours: hours whose issue met the deadline, with both forecasts finite and a measured value. Missed issues,
  missing hours and their count are reported, never imputed.
- Range: the interval score at α = 0.10 (MW), paired experimental − official, with a 95 % week-block bootstrap CI;
  coverage above and below reported beside it.
- Point: MAPE, paired, same CI.
- Verdict for each: "experimental better" or "official better" when the CI excludes 0, else "not separated". The cold
  slice (hours with forecast < −11 °C) is reported and decides nothing.
- Limit: the evidence for the experimental rule comes from monthly refits (2024–2025 folds); live, both forecasts come
  from one frozen model ageing through the winter. Both hang from the same base model, so the comparison stays fair,
  but its size may differ from the folds'.

## Before the tag
- Rehearse issue → log → score on a seen day of 2025 (`uv run python -m live.issue --rehearse <day>`).
- A fresh-context review of this section and the freeze code.
- New code lives in its own package and calls `valiwatt.forecast`; v2's modules, `config.FREEZES`, `THROUGH_YEARS` and
  their tests stay unchanged (the v3 report stamp hashes tests).

## Out of scope
The intraday "adjusted" line in prototype A3 is a mock. It is not part of the ruling and does not ship as a forecast.

## Build order
1. Walk-forward folds 2024–2025 and the 2026 freeze (official and experimental), tag `rules-frozen-live`.
2. The demand feed (every 15 min) and the daily issue just after midnight, written as static JSON plus the
   append-only log and its hash commitments.
3. The page from prototypes A3/A4, reading the JSON; FR by default, EN switch.
4. The Wallpaper Engine skin on the same files.
