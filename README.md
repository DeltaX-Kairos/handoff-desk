# Handoff Desk

## Judges — start here

**Live:** [handoff.deltaxevaluate.com](https://handoff.deltaxevaluate.com)

Handoff Desk checks which fictional CSV delivery meets the contract, confirms
three meanings, and exports a checked ZIP without sending email.

**One-line path:** pick `final.csv` → **Check delivery** → see the red
`2026-08` vs `2026-09` failure → pick `final_v2.csv` → confirm `id`, `period`
and `amount` → download the checked pack.

The files are intentionally fictional: `final.csv` is the wrong month;
`final_v2.csv` matches the required `2026-09` period. Watch the [90-second
judging path](https://youtu.be/QCZHlowTx1A), then try the live desk.

For agencies finishing data and automation projects: compare selected deliverables
with an explicit client checklist, resolve conflicting versions, and export a
checked delivery package with an unsent email.

This is newly written hackathon development using fictional files. It contains no
private DeltaX source. It is an experimental prototype, not a production service.
No prize or customer revenue has been earned from this prototype.

## Try the judging demo

[Open Handoff Desk](https://handoff.deltaxevaluate.com). No account or payment is required. Start with the clearly marked fictional data; explicitly requesting an investigation sends selected file content to AWS. Each visitor has a separate temporary workspace. Files expire after one hour of inactivity and visitor access ends on service restart.

The demo is funded through October 8, 2026, with a shared allowance of 1,000 model attempts and one investigation at a time. Capacity is limited; this is an experimental judging demo, not a production service.

## Current scope

The deterministic core inspects selected CSVs and produces evidence and archives.
The Strands adapter exposes investigation, dictionary drafting, checking and export
tools. The application, not the model, records a user's version choice. A changed
file must invalidate a previously reviewed package. Required dictionary definitions
are confirmed through application-owned state. Source/output accounting compares
exact identifier occurrence counts, preserving leading zeros and duplicates; it
does not prove equality or correctness of every other field.

Install Python 3.10+ and create a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -q
python server.py --port 18765
```

Open `http://127.0.0.1:18765/`. These steps do not invoke a model. Model calls
require separately authorized provider access; importing `agent.py` is offline.

Run `python demo.py output/my-first-demo` to produce a fictional package and a
step-by-step evidence record. Use a new output directory each time. The demo uses
scripted user decisions through real SDK tool wrappers; it is **not** a live model
demonstration. It begins unresolved, records the September file selection, requires
field definitions, exports the checked package, and rejects a later mutation.

## Interactive local preview

Run `python server.py --port 18765` and open `http://127.0.0.1:18765/`.
The page starts with four fictional files and also accepts explicitly selected CSVs. It provides a version choice, editable column meanings,
check results and a ZIP download. It uses the real tool wrappers without invoking
a language model. Changes invalidate the previous download; edited definitions
must be confirmed before exporting again. No email is sent.

The server binds only to loopback and checks the request host, origin and a session
token for mutations. By default, session data disappears when the process stops.
Add `--session-dir output/local-session` to retain project files and confirmed
choices on this Mac. The application saves private local JSON, checks every selected
file's hash and the checklist on recovery, and invalidates mismatched confirmations.
Reviews and downloads are never restored; run a fresh check after restarting.
This is a single-user prototype, not a multi-user hosting configuration.
See [the architecture](ARCHITECTURE.md) for implemented and pending boundaries.

## Bounded Bedrock connection

`python live_agent.py --transport bedrock-mantle --model openai.gpt-oss-20b --region us-east-2`
prints an offline configuration check. It makes no cloud calls. Only adding
`--execute` starts the interactive model session; this requires an authorized
credential supplied through `AWS_BEARER_TOKEN_BEDROCK` in the process
environment. Never commit a credential or include it in a command argument.

The session allows at most six attempted model calls, 1,024 output tokens per
call and an input estimate of at most 8,000 tokens. Unknown estimates are refused.
Automatic retries are disabled. The deadline restarts for each human investigation request; the six-call allowance does not reset while reading or confirming results. Network timeouts and a deadline check before
each call bound activity; they are not a hard wall-clock timeout or dollar cap.
The CLI uses only the original fictional files. Human `/choose` and `/define`
commands record confirmations outside the model's tools. Outputs are temporary.
Offline SDK tests verify cancellation. A separate live fictional-data investigation
has now succeeded through AWS Mantle with `openai.gpt-oss-20b`: the model used the
tools, identified the version conflict and left human confirmations unchanged.
That result does not establish quality across other models, files or scenarios.

The browser investigation panel is disabled by default. To explicitly enable it
after configuring the authorized credential, start the server with
`--bedrock-transport bedrock-mantle --bedrock-model openai.gpt-oss-20b --bedrock-region us-east-2`.
The provider is constructed lazily on the first investigation request; subsequent
requests reuse the same model-call budget. Provider errors are sanitized. Every
investigation clears the export review, so the user must run the delivery check
again. This connection path is covered by offline tests; a live AWS invocation
was also verified through the actual local HTTP route using fictional files.

For a key generated in the Mantle console, use
`--bedrock-transport bedrock-mantle --bedrock-model openai.gpt-oss-20b --bedrock-region us-east-2`.
The saved account key was accepted by Mantle and rejected by the runtime endpoint;
do not treat those authentication paths as interchangeable. The adapter explicitly
targets AWS; it does not use an unrelated OpenAI account.

## Selected CSV projects

Upload one to eight UTF-8 CSV candidate versions of a single deliverable, up to 512 KiB per file and 2 MiB total. Specify required columns and an optional reporting period. The interface builds previews and definition fields from those files. Selection and definitions remain human decisions. Uploading alone makes no cloud call; an explicitly requested investigation may send file contents to AWS. Use only data you are permitted to process there. Source/output row accounting is not inferred for uploaded projects.

## Scope and limits

- The public judging URL, fictional-data video, source repository and contest submission are live.
- No real customer files, business correctness, client acceptance, prize or customer revenue are claimed.

Prototype tool count is bounded, but it is not a provider-dollar spending cap.
Do not run paid inference until model, permissions and usage limits are configured.

## Hosted judging prototype

A separate visitor-isolated WSGI gateway is implemented in `hosted.py`; do not expose the single-user preview directly. See [HOSTING.md](HOSTING.md) for explicit operator configuration, durable aggregate model admission, tested boundaries and operational limits. The full suite passes 69 tests, including a run on the AWS Ubuntu host. Public HTTPS checks verified separate visitors, CSV upload/export, one live AWS investigation and restart cleanup with the consumed aggregate allowance preserved. See HOSTING.md for the scope of this evidence.
