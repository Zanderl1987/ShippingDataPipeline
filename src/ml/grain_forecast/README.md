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

Seasons 2015/16 to 2025/26 (11 per row):

| Crop | Stage | Last season | Pace | USDA | Forecast | Final inside range |
|---|---|---|---|---|---|---|
| Soybeans | weeks 1-8 (Sep-Oct) | 11.2 | 19.1 | 7.8 | **5.7** | 98% |
| Soybeans | weeks 9-17 (Nov-Dec) | 11.2 | 12.6 | 6.8 | **4.8** | 100% |
| Soybeans | weeks 18-30 (Jan-Mar) | 11.2 | 6.5 | **4.1** | 4.5 | 100% |
| Soybeans | weeks 31-53 (Apr-Aug) | 11.2 | 1.5 | 2.4 | **1.3** | 88% |
| Corn | weeks 1-8 (Sep-Oct) | 16.8 | 28.4 | **15.4** | **15.4** | 75% |
| Corn | weeks 9-17 (Nov-Dec) | 16.8 | 16.9 | **9.5** | **9.5** | 74% |
| Corn | weeks 18-30 (Jan-Mar) | 16.8 | 8.1 | **6.0** | **6.0** | 85% |
| Corn | weeks 31-53 (Apr-Aug) | 16.8 | **1.7** | 3.1 | 3.1 | 78% |

- **Soybeans:** the forecast beats USDA's projection in three of four stages.
  In Jan-Mar its median miss is 0.4 points worse (4.5 vs 4.1%), within noise
  over 11 seasons. Its average miss is lower at every stage: 7.3 vs 8.4% in
  Sep-Oct, 5.2 vs 6.2% in Nov-Dec, 4.8 vs 5.2% in Jan-Mar, 2.0 vs 2.7% in
  Apr-Aug. Its worst miss is never larger: 19.9 vs 28.1% in Sep-Oct.
- **Corn:** moving USDA's projection toward pace made it worse before April.
  Corn's sales pace generalizes poorly: its totals swing far more than
  soybeans' (+110% after the 2012 drought, -62% the next season). From April
  pace alone is better (1.7% vs 3.1%). Both are within about 3% by then, so
  the rule was not tuned further to these seasons.
- **Likely range:** it is cautious for soybeans, where the final total almost
  always fell inside it. For corn it held the final total about 3 times in 4.

### Change of WASDE source (2026-10-01)

The figures above use WASDE reports from USDA's ESMIS library, which the
pipeline now collects (usda.gov blocks GitHub's servers). The first version of
these results used usda.gov's CSV copies, as collected then. That collection
lacked five reports (January-April and November 2021) and had five more from
2010 (April-August, before ESMIS has XML). With the complete 2021 record,
USDA's own Jan-Mar soybean miss falls from 4.3 to 4.1%, and the forecast's
rises from 4.0 to 4.5%, so the earlier "beats USDA at every stage" did not
hold. The buyer-mix tests below were run on the earlier data.

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
- **The buyer mix**, tested four ways against the USDA-anchored forecast,
  2014-2025:
  - **China's share of commitments, against its usual share at that week.**
    No gain.
  - **China plus UNKNOWN (often China), against its usual share.** No gain.
  - **Buyer concentration (sum of squared shares), against usual.** No gain.
  - **A pace estimate computed separately for China plus UNKNOWN and for
    everyone else,** each with its own usual booking timing.

  The split pace is the interesting one. On its own it is much better than
  plain pace for soybeans early in the season: 12.0 vs 17.3% median miss in
  Sep-Oct, 6.1 vs 12.1% in Nov-Dec. So China's early booking is real. But
  inside the forecast it did not help: the soybean average miss went from 7.3
  to 8.0% in Sep-Oct, and the worst miss from 21 to 30%. USDA's projection
  evidently already reflects who is buying. For corn there were only 7
  seasons with enough Chinese buying to test. The buyer mix stays on the
  dashboard for monitoring: `grain_export_destinations.share_avg5_pct` is
  each buyer's usual share.

About sixteen variants were compared on the same seasons, which is some
selection risk. The model kept has at most two parameters per week.

## Limits and next steps

- **Short USDA history:** WASDE vintages start in September 2010, so the USDA
  version is tested on only 11 seasons.
- **Different measure from USDA:** the target is the export sales program's
  season total. USDA's figure, and Census customs totals, run a few percent
  higher.
