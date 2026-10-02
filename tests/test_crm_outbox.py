from __future__ import annotations

import json

import crm_store


def test_failed_delivery_stays_in_outbox(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    sent = outbox / "sent"
    monkeypatch.setattr(crm_store, "OUTBOX_DIR", str(outbox))
    monkeypatch.setattr(crm_store, "SENT_DIR", str(sent))

    message_id = "test-message"
    path = outbox / f"{message_id}.json"
    outbox.mkdir()
    path.write_text(
        json.dumps({"id": message_id, "user_id": "42", "text": "hello"}),
        encoding="utf-8",
    )

    payload = crm_store.pending_messages()[0]
    crm_store.mark_sent(payload, error="temporary MAX outage")

    assert path.exists()
    assert not (sent / f"{message_id}.json").exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["attempts"] == 1
    assert saved["last_error"] == "temporary MAX outage"


def test_successful_delivery_moves_to_sent(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    sent = outbox / "sent"
    monkeypatch.setattr(crm_store, "OUTBOX_DIR", str(outbox))
    monkeypatch.setattr(crm_store, "SENT_DIR", str(sent))

    message_id = "test-message"
    path = outbox / f"{message_id}.json"
    outbox.mkdir()
    path.write_text(
        json.dumps({"id": message_id, "user_id": "42", "text": "hello"}),
        encoding="utf-8",
    )

    payload = crm_store.pending_messages()[0]
    crm_store.mark_sent(payload)

    assert not path.exists()
    saved = json.loads((sent / f"{message_id}.json").read_text(encoding="utf-8"))
    assert saved["delivered"] is True
