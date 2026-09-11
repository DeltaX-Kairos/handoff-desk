# Hosting gateway — deployment candidate

`server.py` remains the original single-user loopback preview. `hosted.py` provides a separate WSGI gateway with private visitor sessions. The gateway has passed application checks and a real local Gunicorn HTTP check; public HTTPS, AWS deployment and live inference through this gateway have not been verified.

## Runtime configuration

Install `requirements-hosting.txt` in the project's virtual environment. Run one Gunicorn worker with a small thread pool behind an HTTPS reverse proxy. Multiple workers or replicas are unsupported because visitor state and the concurrency semaphore are process-local.

Required operator environment:

- `HANDOFF_PUBLIC_ORIGIN`: exact public HTTPS origin without a trailing slash or path.
- `HANDOFF_DATA_ROOT`: dedicated private directory, mode 0700, outside the source checkout and web root. Keep the same directory across restarts to retain aggregate inference accounting.

For explicitly enabled AWS investigation, also configure `HANDOFF_MODEL=openai.gpt-oss-20b`, `HANDOFF_REGION=us-east-2`, a deliberate positive `HANDOFF_MODEL_CALLS` allowance, and `AWS_BEARER_TOKEN_BEDROCK` through protected operator configuration. There is no default public model allowance. Do not place the key in command arguments, source, public assets, logs, or this document.

Example process command, after configuring the environment:

```
.venv/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:8080 'hosted:create_from_environment()'
```

The HTTPS proxy must retain the configured Host, set suitable body/header/read timeouts and a 3 MiB request limit, restrict direct access to the backend, and terminate TLS. Do not use the single-user `server.py` behind the proxy. The gateway does not trust forwarded host headers.

## Implemented bounds

A Secure, HttpOnly, SameSite cookie identifies a random visitor session. Each session has its own private directory, confirmation token, selected files, human choices, definitions, review, ZIP and model history. Mutations are serialized per visitor. Only one investigation runs at a time in the process.

The gateway admits at most 32 active sessions, 120 actions per session and eight uploaded projects per session. These are resource protections, not a promise of unlimited judging availability. The operator must plan sufficient free judging access and monitor denial rates.

Every permitted model attempt reserves one unit in a durable SQLite allowance before invocation. Failed or uncertain attempts remain counted. New projects, new visitors and process restarts cannot replenish the allowance. Changing the configured maximum against an existing database is refused for explicit operator reconciliation. This is an attempt bound combined with existing token estimates/output limits, not a precise dollar cap. Deleting or replacing the database defeats accounting and must never be part of an automatic restart.

Idle visitor sessions expire after one hour and a background sweep removes their files at roughly one-minute intervals while the worker runs. Visitor access is not restored after process restart. **Crash-orphan directories need an operator retention job before deployment**; the current worker does not reclaim unregistered directories from earlier processes. Do not claim comprehensive retention enforcement yet.

## Validation still required before publication

- Verify actual AWS service and credit eligibility, final resource cost, and credential expiry/rotation through judging.
- Configure and verify HTTPS, proxy limits and process supervision.
- Add and verify crash-orphan cleanup without deleting the model allowance database.
- Repeat two-visitor upload, review, export and isolation checks through the public HTTPS origin.
- Verify one authorized live model request through the gateway and reconcile AWS usage.
- Verify restart preserves aggregate allowance, invalidates old visitor access and does not mix visitors.

Gunicorn 26.2.0 was selected from the official [PyPI release](https://pypi.org/project/gunicorn/26.2.0/). No cloud resource was created by these instructions.
