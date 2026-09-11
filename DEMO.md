# Handoff Desk demonstration

This walkthrough uses fictional data. It demonstrates a delivery review, not client acceptance or payment. Target recording length: four minutes.

## 0:00–0:35 — The delivery problem

Open the local or deployed application. Explain: an agency has two candidate final files. Their names do not prove which file meets the agreed reporting period. The client also needs clear column definitions and a reproducible handoff.

Show the fictional-data label and required September reporting period. Preview both candidate files. `final.csv` contains August rows; `final_v2.csv` contains September rows.

## 0:35–1:30 — Evidence-driven investigation

With authorized AWS inference configured, ask: “Inspect the candidate files against the checklist. Explain what prevents a checked export and ask me for any decisions you cannot make.”

Show the actual returned answer. Do not replace a failed live result with scripted text. Explain that Strands gives the model selected-file inspection and review tools, while the application retains human version choices and definitions. If inference is unavailable, label this part unavailable and demonstrate deterministic checks separately.

## 1:30–2:30 — Human decisions and review

Select `final_v2.csv`. Run the delivery check and show that column definitions are still required. Confirm these fictional meanings:

- `id`: Source customer identifier; leading zeros are significant.
- `period`: Reporting month in YYYY-MM format.
- `amount`: Fictional invoiced amount in USD.

Run a fresh check. Explain that the configured example accounts for every source identifier occurrence across the final and exceptions files, including repeated `001` and the distinct identifier `1`. This check does not establish correctness of other row values.

## 2:30–3:15 — Inspect the package

Export and open the ZIP. Show the chosen final CSV, exceptions CSV, confirmed dictionary, hash manifest and email draft marked unsent. The reference source and rejected version are excluded. No email was sent and no client acceptance is implied.

## 3:15–4:00 — Your own files and scope

Show the selected-CSV upload form. Explain that a user can supply candidate versions, required columns and an optional period. This mode does not infer source/output accounting. Uploading makes no model call; an explicit investigation can send selected file contents to AWS.

Close with the scope: the judging prototype supports bounded CSV handoff review at https://handoff.deltaxevaluate.com. Its temporary visitor sessions and shared inference allowance are limited; this is not a production service.

## Reproduce without cloud access

Install the pinned requirements in a virtual environment. Run `python -m unittest discover -q`, then `python demo.py output/recording-example` with a new output directory. This produces `delivery.zip` and `scenario.json`, including a rejected export after a file changes. It uses real Strands tool wrappers with scripted human choices and no model calls.
