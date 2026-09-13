#!/usr/bin/env python3
"""Durable PostgreSQL outbound queue for MAX messages."""
from __future__ import annotations
import hashlib,json,os,socket,uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime,timezone
from typing import Any,Iterator
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
DEFAULT_LEASE_SECONDS=60
DEFAULT_MAX_ATTEMPTS=12
DEFAULT_BATCH_SIZE=20
DEFAULT_SENT_RETENTION_SECONDS=30*24*60*60
_REDACTED_PAYLOAD={"kind":"redacted"}
def database_url():
 value=(os.getenv("SDUT_DATABASE_URL") or "").strip()
 if not value: raise RuntimeError("SDUT_DATABASE_URL не задан")
 return value
def _connect(db_url=None): return psycopg.connect(db_url or database_url(),row_factory=dict_row)
def delivery_key(event_id,ordinal=0):
 if not event_id or not str(event_id).strip(): raise ValueError("event_id must not be empty")
 if ordinal<0: raise ValueError("ordinal must be >= 0")
 return f"{str(event_id).strip()}:out:{ordinal}"
def payload_sha256(payload):
 if not isinstance(payload,dict): raise TypeError("payload must be a dict")
 return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def safe_error_code(error):
 if isinstance(error,BaseException): class_name,raw=type(error).__name__,str(error)
 else: class_name,raw="ProviderError",str(error)
 return f"provider_error:{class_name}:{hashlib.sha256(raw.encode('utf-8',errors='replace')).hexdigest()[:16]}"
@dataclass(frozen=True)
class OutboxMessage:
 id:int; delivery_key:str; user_id:str; chat_id:str|None; payload:dict[str,Any]; status:str; attempts:int; available_at:datetime; last_error:str|None
class PostgresOutbox:
 def __init__(self,db_url=None,*,lease_seconds=DEFAULT_LEASE_SECONDS,max_attempts=DEFAULT_MAX_ATTEMPTS,worker_id=None):
  if lease_seconds<=0: raise ValueError("lease_seconds must be > 0")
  if max_attempts<1: raise ValueError("max_attempts must be >= 1")
  self.db_url=db_url or database_url(); self.lease_seconds=lease_seconds; self.max_attempts=max_attempts; self.worker_id=worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
 def _connect(self): return _connect(self.db_url)
 def enqueue(self,*,delivery_key,user_id,payload,chat_id=None,conn=None):
  key=str(delivery_key).strip(); uid=str(user_id).strip()
  if not key: raise ValueError("delivery_key must not be empty")
  if not uid: raise ValueError("user_id must not be empty")
  if not isinstance(payload,dict): raise TypeError("payload must be a dict")
  digest=payload_sha256(payload); own=conn is None; connection=conn or _connect(self.db_url)
  # The caller's transaction connection owns the row factory, and the
  # production state connection uses tuple_row. Read every row through an
  # explicit dict cursor so enqueue works on any caller's connection.
  def ask(sql,params):
   with connection.cursor(row_factory=dict_row) as cur: return cur.execute(sql,params).fetchone()
  try:
   if ask("SELECT 1 FROM deleted_users WHERE user_id=%s",(uid,)) is not None: raise RuntimeError("cannot enqueue outbound message for deleted user")
   tombstone=ask("SELECT payload_sha256 FROM outbox_delivery_tombstones WHERE delivery_key=%s",(key,))
   if tombstone is not None:
    if str(tombstone["payload_sha256"])!=digest: raise ValueError(f"delivery_key collision for {key!r}: retained outbound intent differs")
    if own: connection.commit()
    return 0
   row=ask("INSERT INTO outbox_messages(delivery_key,user_id,chat_id,payload,payload_sha256) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(delivery_key) DO NOTHING RETURNING id",(key,uid,chat_id,Jsonb(payload),digest))
   if row: message_id=int(row["id"])
   else:
    existing=ask("SELECT id,user_id,chat_id,payload,payload_sha256 FROM outbox_messages WHERE delivery_key=%s",(key,))
    if existing is None: raise RuntimeError("outbox insert disappeared unexpectedly")
    existing_payload=existing["payload"]
    if isinstance(existing_payload,str): existing_payload=json.loads(existing_payload)
    existing_chat=str(existing["chat_id"]) if existing["chat_id"] is not None else None; existing_digest=existing["payload_sha256"]
    same_payload=str(existing["user_id"])==uid and existing_chat==chat_id and (str(existing_digest)==digest if existing_digest else dict(existing_payload)==payload)
    if not same_payload: raise ValueError(f"delivery_key collision for {key!r}: existing outbound intent differs")
    message_id=int(existing["id"])
   if own: connection.commit()
   return message_id
  except Exception:
   if own: connection.rollback()
   raise
  finally:
   if own: connection.close()
 @contextmanager
 def user_delivery_lock(self,user_id):
  uid=str(user_id)
  if not uid: raise ValueError("user_id must not be empty")
  with _connect(self.db_url) as conn:
   conn.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))",(uid,))
   try: yield
   finally: conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))",(uid,))
 def claim(self,*,limit=DEFAULT_BATCH_SIZE):
  if limit<1: raise ValueError("limit must be >= 1")
  now=datetime.now(timezone.utc)
  with _connect(self.db_url) as conn:
   stale=conn.execute("SELECT id,payload,payload_sha256,attempts FROM outbox_messages WHERE status='sending' AND locked_at<CURRENT_TIMESTAMP-(%s*INTERVAL '1 second') FOR UPDATE SKIP LOCKED",(self.lease_seconds,)).fetchall()
   for row in stale:
    attempts=int(row["attempts"])
    if attempts>=self.max_attempts:
     digest=str(row["payload_sha256"] or payload_sha256(dict(row["payload"]))); conn.execute("UPDATE outbox_messages SET status='dead',payload=%s,payload_sha256=%s,user_id=NULL,chat_id=NULL,locked_at=NULL,locked_by=NULL,last_error=COALESCE(last_error,'worker lease expired') WHERE id=%s AND status='sending'",(Jsonb(_REDACTED_PAYLOAD),digest,int(row["id"])))
    else: conn.execute("UPDATE outbox_messages SET status='pending',locked_at=NULL,locked_by=NULL WHERE id=%s AND status='sending'",(int(row["id"]),))
   rows=conn.execute("WITH picked AS (SELECT o.id FROM outbox_messages o LEFT JOIN deleted_users d ON d.user_id=o.user_id WHERE o.status='pending' AND o.available_at<=CURRENT_TIMESTAMP AND d.user_id IS NULL ORDER BY o.id FOR UPDATE OF o SKIP LOCKED LIMIT %s) UPDATE outbox_messages o SET status='sending',locked_at=%s,locked_by=%s,attempts=o.attempts+1 FROM picked WHERE o.id=picked.id RETURNING o.*",(limit,now,self.worker_id)).fetchall()
   return [self._row(row) for row in rows]
 def mark_sent(self,message_id):
  with _connect(self.db_url) as conn:
   row=conn.execute("SELECT payload,payload_sha256 FROM outbox_messages WHERE id=%s AND status='sending' AND locked_by=%s FOR UPDATE",(message_id,self.worker_id)).fetchone()
   if row is None: raise RuntimeError(f"outbox message {message_id} is not owned by this worker")
   digest=str(row["payload_sha256"] or payload_sha256(dict(row["payload"])))
   if conn.execute("UPDATE outbox_messages SET status='sent',sent_at=CURRENT_TIMESTAMP,locked_at=NULL,locked_by=NULL,last_error=NULL,user_id=NULL,chat_id=NULL,payload=%s,payload_sha256=%s WHERE id=%s AND status='sending' AND locked_by=%s",(Jsonb(_REDACTED_PAYLOAD),digest,message_id,self.worker_id)).rowcount!=1: raise RuntimeError(f"outbox message {message_id} is not owned by this worker")
 def mark_failed(self,message_id,error):
  safe_error=safe_error_code(error)
  with _connect(self.db_url) as conn:
   row=conn.execute("SELECT payload,payload_sha256,attempts FROM outbox_messages WHERE id=%s AND status='sending' AND locked_by=%s FOR UPDATE",(message_id,self.worker_id)).fetchone()
   if row is None: raise RuntimeError(f"outbox message {message_id} is not owned by this worker")
   attempts=int(row["attempts"])
   if attempts>=self.max_attempts:
    digest=str(row["payload_sha256"] or payload_sha256(dict(row["payload"]))); conn.execute("UPDATE outbox_messages SET status='dead',payload=%s,payload_sha256=%s,last_error=%s,user_id=NULL,chat_id=NULL,locked_at=NULL,locked_by=NULL WHERE id=%s AND status='sending' AND locked_by=%s",(Jsonb(_REDACTED_PAYLOAD),digest,safe_error,message_id,self.worker_id))
   else:
    delay=min(3600,2**min(attempts,10)); conn.execute("UPDATE outbox_messages SET status='pending',available_at=CURRENT_TIMESTAMP+(%s*INTERVAL '1 second'),last_error=%s,locked_at=NULL,locked_by=NULL WHERE id=%s AND status='sending' AND locked_by=%s",(delay,safe_error,message_id,self.worker_id))
 def recover_stale(self):
  with _connect(self.db_url) as conn:
   rows=conn.execute("SELECT id,payload,payload_sha256,attempts FROM outbox_messages WHERE status='sending' AND locked_at<CURRENT_TIMESTAMP-(%s*INTERVAL '1 second') FOR UPDATE SKIP LOCKED",(self.lease_seconds,)).fetchall()
   for row in rows:
    attempts=int(row["attempts"])
    if attempts>=self.max_attempts:
     digest=str(row["payload_sha256"] or payload_sha256(dict(row["payload"]))); conn.execute("UPDATE outbox_messages SET status='dead',payload=%s,payload_sha256=%s,user_id=NULL,chat_id=NULL,locked_at=NULL,locked_by=NULL,last_error=COALESCE(last_error,'worker lease expired') WHERE id=%s AND status='sending'",(Jsonb(_REDACTED_PAYLOAD),digest,int(row["id"])))
    else: conn.execute("UPDATE outbox_messages SET status='pending',locked_at=NULL,locked_by=NULL WHERE id=%s AND status='sending'",(int(row["id"]),))
   return len(rows)
 def prune_sent(self,*,retention_seconds=DEFAULT_SENT_RETENTION_SECONDS,limit=500):
  if retention_seconds<1: raise ValueError("retention_seconds must be >= 1")
  if limit<1: raise ValueError("limit must be >= 1")
  with _connect(self.db_url) as conn:
   rows=conn.execute("SELECT id,delivery_key,payload_sha256 FROM outbox_messages WHERE status='sent' AND sent_at<CURRENT_TIMESTAMP-(%s*INTERVAL '1 second') ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",(retention_seconds,limit)).fetchall()
   for row in rows:
    digest=str(row["payload_sha256"]); existing=conn.execute("SELECT payload_sha256 FROM outbox_delivery_tombstones WHERE delivery_key=%s FOR UPDATE",(row["delivery_key"],)).fetchone()
    if existing is not None and str(existing["payload_sha256"])!=digest: raise ValueError(f"delivery_key collision for {row['delivery_key']!r}: tombstone digest differs")
    conn.execute("INSERT INTO outbox_delivery_tombstones(delivery_key,payload_sha256) VALUES(%s,%s) ON CONFLICT(delivery_key) DO UPDATE SET payload_sha256=EXCLUDED.payload_sha256",(row["delivery_key"],digest)); conn.execute("DELETE FROM outbox_messages WHERE id=%s AND status='sent'",(int(row["id"]),))
   return len(rows)
 def stats(self):
  with _connect(self.db_url) as conn:
   rows=conn.execute("SELECT status,COUNT(*) AS n FROM outbox_messages GROUP BY status").fetchall(); tombstones=conn.execute("SELECT COUNT(*) AS n FROM outbox_delivery_tombstones").fetchone()
  result={str(row["status"]):int(row["n"]) for row in rows}; result["tombstones"]=int(tombstones["n"]); return result
 @staticmethod
 def _row(row):
  payload=row["payload"]
  if isinstance(payload,str): payload=json.loads(payload)
  return OutboxMessage(id=int(row["id"]),delivery_key=str(row["delivery_key"]),user_id=str(row["user_id"]) if row["user_id"] is not None else "",chat_id=str(row["chat_id"]) if row["chat_id"] is not None else None,payload=dict(payload),status=str(row["status"]),attempts=int(row["attempts"]),available_at=row["available_at"],last_error=str(row["last_error"]) if row["last_error"] is not None else None)
