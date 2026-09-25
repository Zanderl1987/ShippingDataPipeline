# Port disruption warning: write-up

A weekly watch list of ports whose ship calls are likely to drop sharply, built from
IMF PortWatch port calls, PortWatch chokepoint transits and GDACS disaster alerts.
Live page: [GitHub Pages](https://zanderl1987.github.io/ShippingDataPipeline/)
(mirror: [HF Space](https://zanderl1337-port-disruption-watch.static.hf.space)).

## The question

PortWatch publishes port calls weekly, on Tuesday, through the previous Friday's week
(4–10 days late). Can we flag, on release day, which ports dropped last week, are
dropping this week, or will drop next week, before the numbers show it?

**What counts as a disruption.** A port-week where calls are at least 30% below the
median of the previous 13 weeks, *and* at least 2.5 "normal swings" below it, so small
ports' noise doesn't count. It also must not repeat a drop from the same week last
year: Christmas–New Year gives ~70 "drops" every year, and those are split out as
seasonal. That leaves 0.76% of port-weeks at ~960 ports with 10+ calls a week.

## What was measured first

- **Most drops are not foreseeable from this data.** GDACS events (cyclones, floods,
  quakes) make a drop 2.7x as likely at a port they list, but explain under 1% of drops.
- **Warnings come as the storm arrives, not weeks before.** So this is a *nowcast*: the
  gain is the reporting lag, not weeks of foresight.
- **The bar to beat is "calls are already falling".** The best simple rule ranks ports
  by how far below normal their last published week was (`last_z`).

## How it works

- 57 inputs per port and week: the port's recent traffic and its drop history, calendar,
  nearby GDACS events (known by release day), the two nearest chokepoints (2 days fresher
  than port data), and whether neighbouring ports dropped.
- One LightGBM model covers all three weeks (last, this, next). The backtest retrains every
  12 weeks, only on outcomes public by then. Chances are rescaled each time against the
  model's own recent track record, so a "30%" means about 30%.
- Every Wednesday, `.github/workflows/warning.yml` retrains on everything published and
  scores the newest week. It then rebuilds the page and appends the week to the
  [warnings history](https://huggingface.co/datasets/ZanderL1337/port-disruption-warnings).
  The whole job runs in about a minute.

## Results (backtest, 2023-01 → 2026-08, 393K port-weeks, 3,313 disruptions)

AP is the area under the precision-recall curve. A random guess scores 0.008. Alerts =
the top 1% of ports for each of the three weeks (about 7 each, 22 in all).

| | Model | Best simple rule |
|---|---|---|
| AP, all | **0.078–0.085** | 0.031 |
| Alerts that were right | **11.8%** | 7.9% |
| Disruptions caught by alerts | **14.7%** | 9.9% |
| Last week / this week / next week AP | 0.138 / 0.076 / 0.040 | 0.055 / 0.033 / 0.017 |

(AP is 0.085 before rescaling the chances and 0.078 after. The alerts are the same either way.)

- **It beats the rule in every year (2023–2026) and at every horizon**, by 1.6–3.5x.
  Removing the holiday weeks still leaves 2.5x.
- **Its chances can be taken at face value:** said 3% → 3.2% happened, 7% → 6.9%,
  27% → 27%, 51% → 41% (a small group).
- **Goals set before training:** AP ≥ 0.045 ✅. Beat the rule every year and horizon ✅.
  15% of alerts right for last and this week ❌: 14.3% (last week 16.9%, this week 11.7%).

## What works and what doesn't

**Works:** a drop that has started, or a region under stress. At Hormuz in 2026, 46%
of Gulf alerts were right, and 31% of the Gulf's disruptions were flagged. Khalifa Port
was caught 11 times out of 18 and Fujairah 9 out of 12.

**Doesn't:**
- **Typhoons, the biggest events in the data.** Shanshan (2024-08, 105 disrupted
  port-weeks) and Lan (2023-08, 93) are missing from the GDACS feed. The model caught 1
  of those ~200. Better storm data is the obvious next input (weather, tracked separately).
- **Slow shifts.** The Red Sea diversions from 2023-12 happened gradually. The 13-week
  "usual" level followed them down, so Aqaba and Djibouti never count as disrupted. This
  is a limit of the label, not the model.
- **Next week.** At that horizon every method is close to guessing (AP 0.040).
- **Most alerts are wrong.** About 1 in 8 is right. It is a ranked watch list, not an alarm.

## Is it worth running, and should it send alerts?

**Keep the weekly job.** It costs about a minute of CI a week. It beats every simple rule
out of sample, and its chances are honest.

**Don't push every alert to Slack/Discord.** With ~22 alerts a week and 1 in 8 right, a
channel would teach people to ignore it. The code has Slack/Discord support
(`src/monitoring/notify.py`), but the repo has no webhook secrets set, so nothing is
sent today. If alerts are wanted, the best option is a **high-confidence digest**:
only ports given 40%+ (in the backtest, 0.45 a week, 41% right; 30%+ would be 1.3 a
week, 37% right), plus a note when
several ports in one region are flagged together.

**Before trusting it more:** compare the live warnings with what actually happened
once ~12 weekly releases have come in (the first live week was 2026-09-07). The
backtest used today's PortWatch history, which may have been revised since first
published.

## Code

`labels.py` (the disruption label), `baselines.py` and `backtest.py` (simple rules),
`features.py`, `train.py` (backtest, live page at `localhost:8765/disruption/`),
`predict.py` (weekly run), `dashboard.py`. Tests in `tests/ml/test_disruption_*.py`.
