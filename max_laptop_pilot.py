"""Local MAX pilot dispatcher with resumable questionnaire UX."""
from __future__ import annotations
import asyncio
import logging
from chatbot_survey import CONSENT_SHORT
from storage_sqlite import SQLiteSurvey
from survey_questions import CHECKPOINT_ID, QUESTIONS

log = logging.getLogger("сдут-бот")

class LaptopSurvey(SQLiteSurvey):
    def continue_detailed(self, user_id: str) -> str:
        uid = str(user_id)
        person = self.state.get(uid)
        if not person or not person.get("finished"):
            return self.question_text(uid)
        checkpoint = next(i for i,q in enumerate(QUESTIONS) if q["id"] == CHECKPOINT_ID)
        keep = {q["id"] for q in QUESTIONS[:checkpoint+1]} | {"district","lift"}
        person["answers"] = {k:v for k,v in (person.get("answers") or {}).items() if k in keep}
        person["answers"][CHECKPOINT_ID] = "Продолжить"
        person["step"] = self._next(checkpoint+1, person["answers"])
        person["history"] = [s for s in (person.get("history") or []) if s < checkpoint+1]
        person["pending"] = None
        person["alerts"] = []
        person["finished"] = None
        person["reading"] = False
        self.save()
        return "Основные данные уже сохранены — имя, телефон и адрес повторно вводить не нужно.\n\n" + self.question_text(uid)

def consent_rows(full=False):
    if full:
        return [[("Согласен, продолжим","c:y")],[("Не согласен","c:n")],[("← Вернуться к краткому тексту","c:back")]]
    return [[("Согласен, продолжим","c:y")],[("Прочитать полный текст","c:full")],[("Не согласен","c:n")],[("Просто почитать","map")]]

def rows(survey,user_id):
    uid=str(user_id)
    if survey.stage(uid)=="consent": return consent_rows(bool(survey.reading(uid)))
    if survey.current(uid) is None:
        return [[("Мои ответы","m")],[("Продолжить подробную анкету","n")],[("Заполнить заново","r")]] if (survey.state.get(uid) or {}).get("finished") else [[("Мои ответы","m")]]
    spot=survey.current(uid); step,q=spot
    if q["kind"]!="choice": return []
    out=[]; multi=bool(q.get("multi")); picked=survey.picked(uid,step) if multi else []; nothing=survey.none_index(q) if multi else None
    shown=[i for i in range(len(q["options"])) if i!=nothing]
    for i in range(0,len(shown),2 if len(shown)>=3 else 1):
        group=shown[i:i+(2 if len(shown)>=3 else 1)]
        out.append([(("✅ " if multi and j in picked else "")+q["options"][j], f"t:{step}:{j}" if multi else f"a:{step}:{j}") for j in group])
    bottom=[]
    if (survey.state.get(uid) or {}).get("history"): bottom.append(("← Назад","b"))
    if multi and picked: bottom.append((f"Готово · {len(picked)}",f"d:{step}"))
    elif multi and nothing is not None: bottom.append((q["options"][nothing],f"a:{step}:{nothing}"))
    elif not q.get("required",True): bottom.append(("Пропустить",f"s:{step}"))
    if bottom: out.append(bottom)
    return out

def markup(data):
    from maxapi.enums.intent import Intent
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
    kb=InlineKeyboardBuilder()
    for row in data: kb.row(*[CallbackButton(text=a,payload=b,intent=Intent.POSITIVE if b in {"c:y","n"} else Intent.DEFAULT) for a,b in row])
    return kb.as_markup()

async def send(bot,chat_id,user_id,text,keyboard=None):
    kwargs={"text":text,"attachments":[keyboard] if keyboard else None}
    if chat_id is not None: kwargs["chat_id"]=chat_id
    else: kwargs["user_id"]=int(user_id)
    await bot.send_message(**kwargs)

def build_dispatcher(survey):
    from maxapi import Dispatcher
    from maxapi.types import BotStarted,MessageCallback,MessageCreated
    import legacy_max_bot as legacy
    dp=Dispatcher()
    @dp.bot_started()
    async def started(event):
        chat,user=event.get_ids(); uid=str(user); text=survey.start(uid); await send(event.bot,chat,uid,text,markup(rows(survey,uid)))
    @dp.message_created()
    async def message(event):
        chat,user=event.get_ids(); uid=str(user); body=event.message.body; text=((body.text if body else None) or "").strip()
        reply=survey.handle(uid,text)
        if reply: await send(event.bot,chat,uid,reply,markup(rows(survey,uid)))
    @dp.message_callback()
    async def callback(event):
        chat,user=event.get_ids(); uid=str(user); payload=event.callback.payload or ""; p=payload.split(":"); action=p[0] if p else ""
        try: await event.ack(notification="Принято")
        except Exception: pass
        if action=="c" and p[1:2]==["full"]: await send(event.bot,chat,uid,survey.consent_text(uid),markup(consent_rows(True))); return
        if action=="c" and p[1:2]==["back"]: await send(event.bot,chat,uid,CONSENT_SHORT,markup(consent_rows(False))); return
        if action=="c" and p[1:2] in (["y"],["n"]):
            reply=survey.grant_consent(uid) if p[1]=="y" else survey.refuse_consent(uid); await send(event.bot,chat,uid,reply,markup(rows(survey,uid))); return
        if action=="n": reply=survey.continue_detailed(uid); await send(event.bot,chat,uid,reply,markup(rows(survey,uid))); return
        if action=="r": reply=survey.restart_after_consent(uid); await send(event.bot,chat,uid,reply,markup(rows(survey,uid))); return
        if action=="m": await send(event.bot,chat,uid,survey.summary(uid),markup(rows(survey,uid))); return
        if action=="b": reply=survey.handle(uid,"назад"); await send(event.bot,chat,uid,reply,markup(rows(survey,uid))); return
        if action=="s": reply=survey.handle(uid,"далее"); await send(event.bot,chat,uid,reply,markup(rows(survey,uid))); return
        if action=="a" and len(p)==3: reply=survey.answer_by_numbers(uid,[int(p[2])+1]); await send(event.bot,chat,uid,reply,markup(rows(survey,uid))); return
        if action=="t" and len(p)==3: survey.toggle(uid,int(p[1]),int(p[2])); await send(event.bot,chat,uid,survey.question_text(uid),markup(rows(survey,uid))); return
        if action=="d" and len(p)==2:
            picked=survey.picked(uid,int(p[1])); reply=survey.answer_by_numbers(uid,[i+1 for i in picked]) if picked else survey.handle(uid,"далее"); await send(event.bot,chat,uid,reply,markup(rows(survey,uid))); return
    return dp

async def main():
    import legacy_max_bot as legacy
    from maxapi import Bot
    survey=LaptopSurvey(list_options=False)
    bot=Bot(legacy.read_token())
    dp=build_dispatcher(survey)
    try: await dp.start_polling(bot)
    finally:
        close=getattr(bot,"close",None)
        if close:
            result=close()
            if asyncio.iscoroutine(result): await result
