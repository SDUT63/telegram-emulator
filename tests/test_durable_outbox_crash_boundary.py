from __future__ import annotations

import asyncio
from types import SimpleNamespace

from durable_outbox_worker import deliver_once


class FakeBot:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(ok=True)


class MarkSentFailsQueue:
    lease_seconds = 60

    def __init__(self) -> None:
        self.claimed = [
            SimpleNamespace(
                id=17,
                attempts=1,
                user_id="42",
                chat_id=None,
                payload={"kind": "max_text", "text": "hello", "keyboard_rows": None},
            )
        ]
        self.mark_sent_calls = 0
        self.mark_failed_calls = 0

    def claim(self, *, limit: int):
        assert limit == 1
        return list(self.claimed)

    def mark_sent(self, message_id: int) -> None:
        self.mark_sent_calls += 1
        raise RuntimeError("simulated DB failure after MAX accepted the request")

    def mark_failed(self, message_id: int, error: str) -> None:
        self.mark_failed_calls += 1
        raise AssertionError("a successful external send must not be converted to failure")


class ShortLeaseQueue(MarkSentFailsQueue):
    lease_seconds = 34


def test_mark_sent_failure_does_not_immediately_resend() -> None:
    """The crash boundary is at-least-once, but one worker pass must not resend."""
    bot = FakeBot()
    queue = MarkSentFailsQueue()

    claimed = asyncio.run(deliver_once(bot, queue=queue))

    assert claimed == 1
    assert bot.calls == 1
    assert queue.mark_sent_calls == 1
    assert queue.mark_failed_calls == 0


def test_worker_rejects_lease_shorter_than_provider_timeout_budget() -> None:
    bot = FakeBot()
    queue = ShortLeaseQueue()

    try:
        asyncio.run(deliver_once(bot, queue=queue))
    except ValueError as error:
        assert "lease_seconds" in str(error)
    else:
        raise AssertionError("worker must fail closed when the lease can expire during send")

    assert bot.calls == 0
