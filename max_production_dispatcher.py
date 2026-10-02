#!/usr/bin/env python3
"""Canonical production dispatcher for MAX."""
from __future__ import annotations
import hashlib
import logging
from max_ui import ASK_WORDS, files_of
from production_outbox import _OUTBOX_EDIT_TARGET
from storage_postgres import _TX_EVENT
log=logging.getLogger("сдут-бот")
DELETION_WORDS={"удалить","удалите","удалить данные","удалите данные","удалить мои данные","удалите мои данные","удалить анкету","удалите анкету","удалить мои данные и анкету","удалите мои данные и анкету","сотри","сотрите","сотри данные","сотрите данные","забудь меня","забудьте меня","/delete","/удалить"}
def _resolve_article_callback(token:str)->str|None:
    if not token.startswith("@"):return token
    digest=token[1:]
    if len(digest)!=16 or any(ch not in "0123456789abcdef" for ch in digest):return None
    import knowledge
    matches=[a.заголовок for a in knowledge.загрузить() if hashlib.sha256(a.заголовок.encode("utf-8")).hexdigest()[:16]==digest]
    return matches[0] if len(matches)==1 else None
def _event_id(value:object)->str|None:
    if value is None:return None
    value=str(value).strip();return value or None
def _fingerprint_event(prefix:str,*parts:object)->str|None:
    normalized=[]
    for part in parts:
        value=_event_id(part)
        if value is None:return None
        normalized.append(value)
    return f"{prefix}:"+hashlib.sha256("|".join(normalized).encode("utf-8")).hexdigest()
def _callback_event_id(event,callback_id:str,user_id:str)->str|None:
    message=getattr(event,"message",None);body=getattr(message,"body",None)
    return _fingerprint_event("callback",user_id,getattr(event,"chat_id",None),_event_id(getattr(body,"mid",None) or getattr(message,"mid",None)),callback_id,_event_id(getattr(getattr(event,"callback",None),"payload",None)),getattr(event,"timestamp",None))
def _started_event_id(event,user_id:str)->str|None:return _fingerprint_event("bot_started",user_id,getattr(event,"chat_id",None),getattr(event,"timestamp",None))
async def _with_event_id(event_id:str,handler):
    token=_TX_EVENT.set(event_id)
    try:return await handler()
    finally:_TX_EVENT.reset(token)
def _deleted_replay(survey,event_id:str)->bool:
    checker=getattr(survey,"deleted_event_tombstone",None)
    return callable(checker) and checker(event_id) is not None
def build_dispatcher(survey,seen=None):
    del seen
    from maxapi import Dispatcher
    from maxapi.types import BotStarted,MessageCallback,MessageCreated
    dp=Dispatcher()
    async def acknowledge(event,text="Принято"):
        try:await event.ack(notification=text)
        except Exception as error:log.debug("MAX callback acknowledgement failed: %s",type(error).__name__)
    @dp.bot_started()
    async def on_started(event:BotStarted):
        _,user_id=event.get_ids();uid=str(user_id);event_key=_started_event_id(event,uid)
        if event_key is None:log.error("BotStarted rejected: missing stable event fields");return
        if _deleted_replay(survey,event_key):return
        async def mutate():survey.start_event(uid)
        await _with_event_id(event_key,mutate)
    @dp.message_created()
    async def on_message(event:MessageCreated):
        _,user_id=event.get_ids();uid=str(user_id);body=getattr(getattr(event,"message",None),"body",None);event_key=_event_id(getattr(body,"mid",None))
        if event_key is None:log.error("message rejected: missing provider mid");return
        if _deleted_replay(survey,event_key):return
        incoming=(getattr(body,"text",None) or "").strip();files=files_of(body)
        async def mutate():
            normalized=incoming.casefold().strip(" ?!.")
            if normalized in DELETION_WORDS:
                # Keep deletion inside the same event transaction. Its payload
                # is marked as a deletion so the commit path cannot write an
                # audit/outbox record after the user's data has been purged.
                survey._mutate(uid,"message",{"kind":"delete"},lambda: survey.delete_user(uid),None)
                return
            if normalized in ASK_WORDS:survey.handle_navigation_event(uid,"map",[])
            else:survey.handle_message_event(uid,incoming,files)
        await _with_event_id(event_key,mutate)
    @dp.message_callback()
    async def on_button(event:MessageCallback):
        _,user_id=event.get_ids();uid=str(user_id);callback=event.callback;callback_id=_event_id(getattr(callback,"callback_id",None))
        if callback_id is None:log.error("callback rejected: missing button callback_id");return
        event_key=_callback_event_id(event,callback_id,uid)
        if event_key is None:log.error("callback rejected: missing stable event fields");return
        if _deleted_replay(survey,event_key):await acknowledge(event);return
        payload=getattr(callback,"payload",None) or ""
        if len(payload)>512:log.warning("callback rejected: payload exceeds 512 bytes");return
        # The screen the button sits on. Map navigation rewrites it in place so
        # browsing topics behaves like tabs instead of burying the chat.
        source_message=_event_id(getattr(getattr(getattr(event,"message",None),"body",None),"mid",None))
        async def mutate():
            parts=payload.split(":");action=parts[0] if parts else "";args=parts[1:]
            if action=="map":survey.handle_navigation_event(uid,"map",[]);return
            if action=="v" and args:survey.handle_navigation_event(uid,"v",args);return
            if action=="k" and payload.startswith("k:"):
                title=_resolve_article_callback(payload[2:])
                if title is None:log.warning("knowledge callback rejected: invalid or ambiguous token")
                else:survey.handle_navigation_event(uid,"k",[title])
                return
            if action=="q":survey.handle_navigation_event(uid,"q",[]);return
            if action=="c" and args[:1]==["full"]:survey.handle_navigation_event(uid,"cfull",[]);return
            # «r» — заполнить заново. Без него кнопка на экране есть,
            # а нажатие уходит в никуда: диспетчер молча отбрасывает
            # действие, которого нет в этом наборе.
            if action in {"c","a","s","d","t","b","n","r","m"}:survey.handle_callback_event(uid,action,args);return
            log.info("unknown callback action rejected")
        token=_OUTBOX_EDIT_TARGET.set(source_message)
        try:
            await _with_event_id(event_key,mutate)
        finally:
            _OUTBOX_EDIT_TARGET.reset(token)
        await acknowledge(event)
    return dp
__all__=["build_dispatcher","_resolve_article_callback","_callback_event_id","_started_event_id"]
