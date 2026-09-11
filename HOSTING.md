# Hosting gateway — judging deployment

`server.py` remains the original single-user loopback preview. `hosted.py` provides a separate WSGI gateway with private visitor sessions. The gateway is deployed at https://handoff.deltaxevaluate.com on an AWS Lightsail Ubuntu host behind Nginx HTTPS. On September 11, 2026, 34 public HTTP assertions covered visitor cookies, isolation, fictional and uploaded exports, and live AWS investigation. A controlled restart cleared visitor directories and invalidated old access while retaining the consumed aggregate model allowance. The 30-day credential was separately verified with a live investigation after replacement.

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

Idle visitor sessions expire after one hour and a background sweep removes their files at roughly one-minute intervals while the worker runs. Visitor access is not restored after process restart. Before accepting traffic, startup removes all prior visitor directories from the dedicated `sessions` directory while preserving the model allowance database in its parent. An exclusive process lock prevents a second worker from deleting a running worker's files. Stop the old worker before starting its replacement; overlapping graceful worker reloads are unsupported. Do not delete the `.owner.lock` file, which must retain the same inode across restarts.

Startup refuses unexpected names, symlinks in place of visitor directories, and invalid ownership-lock files before removing any visitor directories. Symlinks inside a visitor directory are removed without following their targets. A cleanup failure prevents startup and requires operator inspection. Local restart tests verify old-cookie rejection, orphan removal, external-file preservation and unchanged aggregate accounting. Retention while the service is stopped is enforced at its next successful startup, not by an independent system job.

## Verified deployment and operating limits

The judging host runs one Gunicorn gthread worker with four threads, bound to loopback. Nginx terminates TLS, limits requests to 3 MiB, applies per-address request/connection limits, and uses a 240-second upstream read timeout without upstream retries. A dedicated unprivileged system service owns its private data directory. Credentials are held in protected operator configuration outside the repository; the browser never receives the key.

The initial site-wide allowance is 1,000 model attempts. Failed attempts count. This is not a hard dollar or wall-clock cap: the 180-second application deadline is checked before each model call and cannot interrupt a call already running. No automatic key or budget replenishment occurs. The operator must monitor remaining credit, model allowance and session capacity through judging. Anonymous traffic can exhaust demo capacity.

Use a full service stop/start for application replacements, not a graceful overlapping-worker reload or Gunicorn preload. The exclusive lock prevents a replacement from cleaning a live worker's files. Keep the allowance database and ownership lock across restarts. Stopped-service files are removed on the next successful startup.

The owner approved hosting through October 8, 2026. Server, dedicated IP, DNS record and judging credential must be removed afterward unless renewed by the owner. Free judging access depends on continued AWS credit and allowance availability; the current checks do not prove uninterrupted future uptime.

Gunicorn 26.2.0 was selected from the official [PyPI release](https://pypi.org/project/gunicorn/26.2.0/).
