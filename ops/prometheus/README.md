# Monitoring

The MAX production service exposes `GET /metrics` on the same HTTP listener as
`/health` and `/ready`. Keep this endpoint behind the private monitoring
network or an authenticated reverse proxy; do not expose it as a public API.

Load `alerts.yml` into Prometheus and route alerts through the organisation's
existing Alertmanager/on-call system. This repository deliberately does not
embed Alertmanager credentials or notification endpoints.

Recommended scrape interval: 15–30 seconds.

Minimum production alerts:

- `SDUTStorageDown` — critical;
- `SDUTOutboxDeadMessages` — critical;
- `SDUTOutboxBacklog` — warning;
- `SDUTServiceRestartLoop` — warning.

The metric set contains technical state only. It must not be extended with
MAX user IDs, telephone numbers, questionnaire answers, message bodies, or
other personal/medical data.
