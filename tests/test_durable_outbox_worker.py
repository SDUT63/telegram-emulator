from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone

import durable_outbox_worker
from outbox_postgres import OutboxMessage


class FakeBot:
    def __init__(self, *, fail=False): self.calls=[]; self.fail=fail
    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail: raise RuntimeError("MAX unavailable")


class FakeQueue:
    lease_seconds=60
    worker_id="test-worker"
    db_url=""
    def __init__(self,message): self.message=message;self.failed=[];self.sent=[]
    def claim(self,*,limit=1):
        message,self.message=self.message,None
        return [message] if message is not None else []
    @contextmanager
    def user_delivery_lock(self,user_id): yield
    def attachments_for(self,delivery_key): return []
    def mark_failed(self,message_id,error): self.failed.append((message_id,error))
    def mark_sent(self,message_id): self.sent.append(message_id)


def message(*,payload=None):
    return OutboxMessage(id=7,delivery_key="event:out:0",user_id="123",chat_id=None,payload=payload or {"kind":"max_text","text":"Привет","keyboard_rows":[]},status="sending",attempts=1,available_at=datetime.now(timezone.utc),last_error=None)


def test_deliver_once_marks_success_after_max_accepts_message(monkeypatch):
    monkeypatch.setattr(durable_outbox_worker,"_claim_still_deliverable",lambda *_: True)
    bot=FakeBot();queue=FakeQueue(message())
    assert asyncio.run(durable_outbox_worker.deliver_once(bot,queue=queue))==1
    assert len(bot.calls)==1;assert bot.calls[0]["user_id"]==123;assert bot.calls[0]["text"]=="Привет";assert queue.sent==[7];assert queue.failed==[]


def test_deliver_once_retries_network_failure_without_marking_sent(monkeypatch):
    monkeypatch.setattr(durable_outbox_worker,"_claim_still_deliverable",lambda *_: True)
    bot=FakeBot(fail=True);queue=FakeQueue(message())
    assert asyncio.run(durable_outbox_worker.deliver_once(bot,queue=queue))==1
    assert len(bot.calls)==1;assert queue.sent==[];assert queue.failed==[(7,queue.failed[0][1])]
    assert isinstance(queue.failed[0][1],RuntimeError)


def test_deliver_once_does_not_resend_when_sent_commit_fails(monkeypatch):
    monkeypatch.setattr(durable_outbox_worker,"_claim_still_deliverable",lambda *_: True)
    class SentCommitFailureQueue(FakeQueue):
        def mark_sent(self,message_id): raise RuntimeError("database unavailable")
    bot=FakeBot();queue=SentCommitFailureQueue(message())
    assert asyncio.run(durable_outbox_worker.deliver_once(bot,queue=queue))==1
    assert len(bot.calls)==1;assert queue.failed==[]
