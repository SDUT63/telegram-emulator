"""Small dependency-free Prometheus exposition registry for the MAX service."""
from __future__ import annotations

import threading
import time
from collections import defaultdict


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._started = time.monotonic()

    @staticmethod
    def _labels(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        if not labels:
            return ()
        return tuple(sorted((str(k), str(v).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')) for k, v in labels.items()))

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1) -> None:
        if value < 0:
            raise ValueError("counter increment must be non-negative")
        key = (name, self._labels(labels))
        with self._lock:
            self._counters[key] += value

    def set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = (name, self._labels(labels))
        with self._lock:
            self._gauges[key] = float(value)

    def observe_duration(self, name: str, started: float, labels: dict[str, str] | None = None) -> None:
        self.inc(name, labels, max(0.0, time.monotonic() - started))

    def render(self) -> str:
        with self._lock:
            counters = list(self._counters.items())
            gauges = list(self._gauges.items())
        lines = [
            '# HELP sdut_process_uptime_seconds Process uptime.',
            '# TYPE sdut_process_uptime_seconds gauge',
            f'sdut_process_uptime_seconds {time.monotonic() - self._started:.3f}',
        ]
        # One TYPE line per metric NAME, with every labelled series grouped
        # under it. Prometheus rejects a scrape outright on a second TYPE
        # line for the same name ("second TYPE line for metric name"), so
        # emitting one per series silently broke monitoring as soon as any
        # metric gained a label.
        for kind, series in (("counter", counters), ("gauge", gauges)):
            grouped: dict[str, list[str]] = {}
            for (name, labels), value in series:
                grouped.setdefault(name, []).append(f'{_format(name, labels)} {value:g}')
            for name in sorted(grouped):
                lines.append(f'# TYPE {name} {kind}')
                lines.extend(sorted(grouped[name]))
        return '\n'.join(lines) + '\n'


def _format(name: str, labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return name
    return name + '{' + ','.join(f'{key}="{value}"' for key, value in labels) + '}'


METRICS = Metrics()
