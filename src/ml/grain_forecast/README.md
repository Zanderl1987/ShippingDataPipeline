# Season export forecast: US corn and soybeans

Forecasts how many tons of corn and soybeans the US will export over the
current marketing year (September to August). The forecast updates every week
from USDA weekly export sales. Rebuilt in curation as `grain_export_forecast`
and shown on the data dashboard.

```
python -m src.ml.grain_forecast.backtest          # local pipeline.db
python -m src.ml.grain_forecast.backtest --hf     # read from the HF dataset
```

## How it works

There are two simple estimates at any week of the season:

- **Last season:** the total shipped last season.
- **Pace:** tons committed so far (shipped, plus sold but not yet shipped),
  divided by the share of a season's exports usually committed by this week.

The forecast blends them. A single weight per crop and per week of the season
says how far to trust pace over last season's total. The weight is fit only on
seasons that had finished before the one being forecast. Corn uses pace alone
(see results). The likely range comes from the forecast's own misses on
earlier seasons, each made before that season's outcome was known.

## Results

The backtest covers 18 seasons, 2008/09 to 2025/26. Each season is forecast
using only earlier seasons. The figures are the median miss against the final
total, in percent.

| Crop | Stage of season | Last season | Pace | Forecast | Final inside likely range |
|---|---|---|---|---|---|
| Soybeans | weeks 1-8 (Sep-Oct) | 10.3 | 19.1 | **9.1** | 62% |
| Soybeans | weeks 9-17 (Nov-Dec) | 10.3 | 13.5 | **6.4** | 78% |
| Soybeans | weeks 18-30 (Jan-Mar) | 10.3 | 9.6 | **5.1** | 83% |
| Soybeans | weeks 31-53 (Apr-Aug) | 10.3 | 1.5 | **1.4** | 71% |
| Corn | weeks 1-8 (Sep-Oct) | **18.1** | 26.1 | 26.1 | 71% |
| Corn | weeks 9-17 (Nov-Dec) | 18.1 | 12.2 | **12.2** | 74% |
| Corn | weeks 18-30 (Jan-Mar) | 18.1 | 7.6 | **7.6** | 72% |
| Corn | weeks 31-53 (Apr-Aug) | 18.1 | 1.9 | **1.9** | 71% |

- **Soybeans:** the blend beats both simple estimates at every stage of the
  season. It also does so on the average miss and on the worst miss. Its
  biggest gain is in Nov-Dec, where it halves the error.
- **Corn:** the fitted blend did worse than plain pace at every stage, on
  median, average and worst-case miss. Corn's season totals swing far more
  than soybeans' (+110% after the 2012 drought, -62% the next season). The
  fitted weight hedged toward last season's total, and that cost accuracy in
  ordinary years. So corn uses pace alone. In September and October nothing
  beats repeating last season's total, so the dashboard shows the corn
  forecast in brackets before week 9 (`RELIABLE_FROM_WEEK`).
- **Likely range:** it held the final total about 3 times in 4, not the
  nominal 8 in 10. Only about 13 seasons are old enough to build a range from,
  so treat it as a rough guide.

## What was tried and dropped

These were tested on the same seasons and dropped because they did worse out
of sample:

- **Ridge regressions** on pace, last season, the 5-year average, commitments
  against last season and against the 5-year average, and the unshipped share.
  Fit with both crops pooled, per crop, and relative to pace.
- **Fitting the weight on absolute instead of squared error.** This made corn
  worse.
- **Adding the 5-year-average total as a third blend input.**

About eight variants were compared on the same seasons, which is some
selection risk. The one kept has a single parameter per week.

## Limits and next steps

- **No benchmark against USDA:** its monthly WASDE export projection is the
  real benchmark, and it is not collected yet. Adding it would show whether
  this forecast adds anything to the official number.
- **No buyer mix:** buyer concentration, such as China's share of soybean
  sales, is not used yet. It matters for soybeans: China bought nothing at
  this point in 2025 and holds 48% of 2026/27 sales.
- **Different measure from Census:** the target is USDA's season total of
  exports shipped, which differs slightly from Census customs totals.
