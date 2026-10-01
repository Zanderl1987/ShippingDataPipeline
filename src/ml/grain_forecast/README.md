# Season export forecast: US corn and soybeans

Forecasts the total tons of corn and soybeans the US will export over the
current marketing year (September to August). The forecast updates every week
from USDA weekly export sales and USDA's monthly WASDE report. It is rebuilt
in curation as `grain_export_forecast` and shown on the data dashboard next to
USDA's own projection.

```
python -m src.ml.grain_forecast.backtest          # local pipeline.db
python -m src.ml.grain_forecast.backtest --hf     # read from the HF dataset
```

## How it works

Three estimates are available at any week of the season:

- **USDA:** the US export projection in the latest WASDE report released
  before that week's sales data came out.
- **Pace:** tons committed so far (shipped, plus sold but not yet shipped),
  divided by the share of a season's exports usually committed by this week.
- **Last season:** the total shipped last season.

The forecast starts from USDA's projection. It corrects for USDA counting
exports 2-4% higher than the export sales program. For soybeans it then moves
toward pace. How far it moves, and the size of the correction, are fit per
crop and per week of the season, only on seasons that finished before the one
being forecast.

Corn uses USDA's corrected projection alone. Seasons before 2010 have no
WASDE data, so they start from last season's total instead.

The likely range comes from the forecast's own misses on earlier seasons,
each made before that season's outcome was known.

## Results

Each season is forecast using only earlier seasons. The figures are the median
miss against the final total, in percent. "USDA" is USDA's projection with the
same level correction, so the gap in how the two count exports does not count
against it.

Seasons 2014/15 to 2025/26 (11-12 per row):

| Crop | Stage | Last season | Pace | USDA | Forecast | Final inside range |
|---|---|---|---|---|---|---|
| Soybeans | weeks 1-8 (Sep-Oct) | 11.2 | 19.1 | 7.8 | **5.3** | 100% |
| Soybeans | weeks 9-17 (Nov-Dec) | 11.2 | 12.6 | 6.7 | **4.7** | 100% |
| Soybeans | weeks 18-30 (Jan-Mar) | 11.2 | 6.3 | 4.3 | **4.0** | 100% |
| Soybeans | weeks 31-53 (Apr-Aug) | 10.9 | 1.4 | 1.9 | **1.3** | 89% |
| Corn | weeks 1-8 (Sep-Oct) | 16.8 | 28.4 | **15.4** | **15.4** | 75% |
| Corn | weeks 9-17 (Nov-Dec) | 16.8 | 16.9 | **9.6** | **9.6** | 74% |
| Corn | weeks 18-30 (Jan-Mar) | 16.8 | 8.0 | **6.1** | **6.1** | 85% |
| Corn | weeks 31-53 (Apr-Aug) | 16.3 | **1.8** | 3.1 | 3.1 | 78% |

- **Soybeans:** the forecast beats USDA's projection at every stage of the
  season. Its average miss is also lower at every stage: 7.3 vs 8.4% in
  Sep-Oct, 1.9 vs 2.6% in Apr-Aug. Its worst miss is never larger: 20.6 vs
  27.9% in Sep-Oct.
- **Corn:** moving USDA's projection toward pace made it worse before April.
  Corn's sales pace generalizes poorly: its totals swing far more than
  soybeans' (+110% after the 2012 drought, -62% the next season). From April
  pace alone is better (1.8% vs 3.1%). Both are within about 3% by then, so
  the rule was not tuned further to these seasons.
- **Likely range:** it is cautious for soybeans, where the final total almost
  always fell inside it. For corn it held the final total about 3 times in 4.

### Before USDA was added

The first version blended last season's total with pace, without USDA data.
Over 2008/09 to 2025/26 its median misses were:

- soybeans: 9.1, 6.4, 5.1 and 1.4% by stage;
- corn (pace alone): 26, 12, 7.6 and 1.9%.

USDA as the starting point mostly helps early in the season.

### How USDA compares on its own terms

This compares USDA's published projection with USDA's own final figure, by
release month, over 2010-2025:

| Crop | Sep-Oct | Nov-Dec | Jan-Mar | Apr-Aug |
|---|---|---|---|---|
| Corn | 10% | 7% | 10% | 3% |
| Soybeans | 5% | 5% | 5% | 2% |

## What was tried and dropped

These were tested on the same seasons and dropped because they did worse out
of sample:

- **Ridge regressions** on pace, last season, the 5-year average, commitments
  against last season and against the 5-year average, and the unshipped share.
  Fit with both crops pooled, per crop, and relative to pace.
- **Fitting the blend weight on absolute instead of squared error.**
- **Adding the 5-year-average total as a third blend input.**
- **Fitted pace weights for corn,** both with and without USDA.

About a dozen variants were compared on the same seasons, which is some
selection risk. The model kept has at most two parameters per week.

## Limits and next steps

- **No buyer mix:** buyer concentration, such as China's share of soybean
  sales, is not used yet.
- **Short USDA history:** WASDE vintages start in April 2010, so the USDA
  version is tested on only 12 seasons.
- **Different measure from USDA:** the target is the export sales program's
  season total. USDA's figure, and Census customs totals, run a few percent
  higher.
