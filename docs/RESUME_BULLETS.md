# Resume bullets, project description, pitch

Every number below comes from a generated file or command in this repo. Re-run
`python tasks.py check` before using them; if a number changes, update it here.

## Bullets (action → method → measured result)

1. **Built a rule-based AWS cost-optimization advisor** (Python, Pandas, Pydantic,
   Streamlit) that recommends rightsizing and off-hours shutdowns only behind a
   data-quality gate and explicit evidence grades, never treating missing metrics as zero.
   Evaluated on **88 labelled synthetic scenarios (176 decisions)** with **0 safety-invariant
   violations**.
   - *Sources:* 88 / 176 → `reports/evaluation_report_v1.md` (header table); 0 violations →
     `reports/evaluation_report.md` §A ("Safety violations" column).

2. **Designed a label-first, pre-registered evaluation:** ground-truth labels committed
   before the rules, and predicted misses committed before the evaluation code. On synthetic
   scenarios the rules made **3 errors in 176 decisions, all 3 pre-registered**, scoring
   **160/160 on non-ambiguous and 13/16 on deliberately ambiguous cases**.
   - *Sources:* `reports/evaluation_report_v1.md` §1 (outcome misses, "By subset") and §3
     ("Predicted vs actual misses"); commit order in `docs/EVALUATION.md` §2.

3. **Diagnosed the root cause** (risk signals re-ranked but never blocked actions), and
   added two human-review gates validated on a **29-resource held-out synthetic set
   committed before the fix**. Held-out errors fell from **15/58 to 0/58**, and
   original-set RECOMMEND precision rose to **1.000** for both rules. Also reported the
   **one over-trigger** the fix introduced.
   - *Sources:* `reports/evaluation_report.md` §A (both tables) and §B (`ec2-0070` row).

4. **Shipped it test-first:** **168 pytest tests at 97% line+branch coverage** (lint, tests,
   reproducible-data check and evaluation in a GitHub Actions workflow), with savings
   arithmetic matching **13/13 independently hand-computed cases**.
   - *Sources:* 168 / 97.27% → `python tasks.py test` output (2026-11-01; not a committed
     file, so re-run before citing); 13/13 → `reports/evaluation_report.md` §D ("Savings
     checks passed").

Always keep the words **"synthetic scenarios"** next to any accuracy number. Until the repo
is pushed and CI has run green, say "GitHub Actions workflow", not "passing CI".

## LinkedIn / GitHub description (2 lines)

> Rule-based AWS cost-optimization advisor that recommends rightsizing and off-hours
> shutdowns only when the evidence justifies it, and says "insufficient data" when it doesn't.
> Python · Pandas · Pydantic · Streamlit; label-first, pre-registered evaluation on synthetic scenarios.

## 60-second pitch

> "Cloud cost tools often recommend savings on thin evidence, like a few days of metrics or
> gaps read as idle time. I built a rule-based advisor that only recommends downsizing or
> off-hours shutdowns when the data passes a quality gate, grades its evidence, and
> attaches risks and an estimated saving with its formula.
>
> The part I'm proudest of is the evaluation. I wrote ground-truth labels before the rules,
> and before running the evaluation I committed which cases I expected it to get wrong.
> It made 3 errors in 176 decisions on synthetic scenarios, all 3 predicted.
>
> All three had one root cause: the system detected the danger but only used it for
> ranking. So I added two human-review gates. I validated them on a held-out set I
> committed before writing the fix: errors went from 15 out of 58 to zero. I also reported
> the one new false alarm the fix caused.
>
> It's 168 tests at 97% coverage. The honest caveat is that it's all synthetic and I wrote
> the scenarios myself, so it proves the logic, not real-world accuracy. The next step is
> read-only AWS adapters."
