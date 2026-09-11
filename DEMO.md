# Handoff Desk demonstration

This walkthrough uses fictional CSVs. It demonstrates a delivery review, not client acceptance or payment. Target recording length: 90 seconds. The published cut is [here](https://youtu.be/DV4hOw7MVfA).

## 0:00–0:12 — Start with the mismatch

Open [the live desk](https://handoff.deltaxevaluate.com). Say: “Handoff Desk is a review desk for a delivery that returned 200 but still violates the contract.” The first screen shows two candidate files and no choice selected.

Point to the captions: `final.csv` is the wrong month; `final_v2.csv` matches `2026-09`.

## 0:12–0:38 — Make the failure undeniable

Choose `final.csv`, click **Check delivery**, and show the red failure box: “period is `2026-08`, contract wants `2026-09`,” including `(001, 2026-08)`. The application refuses to package the wrong delivery.

Optionally ask the bounded agent: “Which file is the delivery and why might 001 and 1 both appear?” AWS may read the selected files, but it cannot choose the delivery or mark it complete.

## 0:38–1:10 — Confirm meaning, then recheck

Select `final_v2.csv`. Confirm the three meanings in one screen:

- `id`: Source customer identifier; leading zeros are significant.
- `period`: Reporting month in YYYY-MM format.
- `amount`: Fictional invoiced amount in USD.

Click **Confirm these meanings and recheck**. The desk goes green only for the matching file. Explain that repeated `001` and the distinct identifier `1` remain distinct; this check does not establish correctness of every other row value.

## 1:10–1:30 — Deliver evidence

Click **Download checked pack (ZIP)**. Show the chosen CSV, exceptions CSV, confirmed dictionary, hash manifest and unsent email draft. Close with: “Nothing was emailed. The reviewer gets evidence they can defend.”

## Optional after the 90-second cut

Show the selected-CSV upload form only if asked. Users can supply candidate versions, required columns and an optional period. Uploading makes no model call; an explicit investigation can send selected file contents to AWS.

The judging prototype supports bounded CSV handoff review at https://handoff.deltaxevaluate.com. Its temporary visitor sessions and shared inference allowance are limited; this is not a production service.

## Reproduce without cloud access

Install the pinned requirements in a virtual environment. Run `python -m unittest discover -q`, then `python demo.py output/recording-example` with a new output directory. This produces `delivery.zip` and `scenario.json`, including a rejected export after a file changes. It uses real Strands tool wrappers with scripted human choices and no model calls.
