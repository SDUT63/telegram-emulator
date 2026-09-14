"""Complete local MAX pilot dispatcher."""
from __future__ import annotations
import asyncio
from chatbot_survey import CONSENT_SHORT, CONSENT_NO, RESUMED, ALREADY_DONE
from storage_sqlite import SQLiteSurvey
from survey_questions import CHECKPOINT_ID, QUESTIONS

class LaptopSurvey(SQLiteSurvey):
    def continue_detailed(self,user_id):
        uid=str(user_id); p=self.state.get(uid)
        if not p or not p.get("finished"): return self.question_text(uid)
        cp=next(i for i,q in enumerate(QUESTIONS) if q["id"]==CHECKPOINT_ID)
        keep={q["id"] for q in QUESTIONS[:cp+1]}|{"district","lift"}
        p["answers"]={k:v for k,v in (p.get("answers") or {}).items() if k in keep}
        p["answers"][CHECKPOINT_ID]="Продолжить"; p["step"]=self._next(cp+1,p["answers"])
        p["history"]=[s for s in (p.get("history") or []) if s<cp+1]; p["pending"]=None
        p["alerts"]=[]; p["finished"]=None; p["reading"]=False; self.save()
        return "Основные данные уже сохранены — имя, телефон и адрес повторно вводить не нужно.\n\n"+self.question_text(uid)

def consent_rows(full=False):
    return [[("Согласен, продолжим","c:y")],[("Не согласен","c:n")],[("← Вернуться к краткому тексту","c:back")]] if full else [[("Согласен, продолжим","c:y")],[("Прочитать полный текст","c:full")],[("Не согласен","c:n")],[("Просто почитать","map")]]

def rows(s,u):
    u=str(u)
    if s.stage(u)=="consent": return consent_rows(bool(s.reading(u)))
    if s.current(u) is None: return [[("Мои ответы","m")],[("Продолжить подробную анкету","n")],[("Заполнить заново","r")]] if (s.state.get(u) or {}).get("finished") else [[("Мои ответы","m")]]
    step,q=s.current(u); out=[]
    if q["kind"]!="choice": return out
    multi=bool(q.get("multi")); picked=s.picked(u,step) if multi else []; nothing=s.none_index(q) if multi else None
    shown=[i for i in range(len(q["options"])) if i!=nothing]; width=2 if len(shown)>=3 else 1
    for n in range(0,len(shown),width):
        g=shown[n:n+width]; out.append([(("✅ " if multi and j in picked else "")+q["options"][j],f"t:{step}:{j}" if multi else f"a:{step}:{j}") for j in g])
    bottom=[]
    if (s.state.get(u) or {}).get("history"): bottom.append(("← Назад","b"))
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

async def send(bot,chat,user,text,rows_=None):
    args={"text":text,"attachments":[markup(rows_)] if rows_ else None}
    if chat is not None: args["chat_id"]=chat
    else: args["user_id"]=int(user)
    await bot.send_message(**args)

def build_dispatcher(s):
    from maxapi import Dispatcher
    from maxapi.types import BotStarted,MessageCallback,MessageCreated
    import legacy_max_bot as old
    dp=Dispatcher()
    @dp.bot_started()
    async def started(e):
        c,u=e.get_ids();u=str(u); p=s.state.get(u)
        if p is None: text=s.start(u)
        elif p.get("finished"): text=ALREADY_DONE
        elif (p.get("consent") or {}).get("at"): text=RESUMED+"\n\n"+s.question_text(u)
        elif (p.get("consent") or {}).get("refused"): text=CONSENT_NO
        else: text=CONSENT_SHORT
        await send(e.bot,c,u,text,rows(s,u))
    @dp.message_created()
    async def message(e):
        c,u=e.get_ids();u=str(u);b=e.message.body;text=((b.text if b else None) or "").strip()
        if text.casefold().strip(" ?!.") in old.ASK_WORDS and u in s.state: await old.меню_тем(e.bot,c,u,s); return
        r=s.handle(u,text)
        if r: await send(e.bot,c,u,r,rows(s,u))
    @dp.message_callback()
    async def callback(e):
        c,u=e.get_ids();u=str(u);p=(e.callback.payload or "").split(":");a=p[0] if p else ""
        try: await e.ack(notification="Принято")
        except Exception: pass
        if a=="c" and p[1:2]==["full"]: await send(e.bot,c,u,s.consent_text(u),consent_rows(True)); return
        if a=="c" and p[1:2]==["back"]: await send(e.bot,c,u,CONSENT_SHORT,consent_rows(False)); return
        if a=="c" and p[1:2] in (["y"],["n"]):
            r=s.grant_consent(u) if p[1]=="y" else s.refuse_consent(u); await send(e.bot,c,u,r,rows(s,u)); return
        if a=="map": await old.меню_тем(e.bot,c,u,s); return
        if a=="v" and len(p)>=2: await old.ветвь(e.bot,c,u,p[1],int(p[2]) if len(p)>2 and p[2].isdigit() else 1,s); return
        if a=="k": await old.статья(e.bot,c,u,p[1] if len(p)>1 else "",s); return
        if a=="q": await send(e.bot,c,u,s.question_text(u) if s.current(u) else s.summary(u),rows(s,u)); return
        if a=="n": await send(e.bot,c,u,s.continue_detailed(u),rows(s,u)); return
        if a=="r": await send(e.bot,c,u,s.restart_after_consent(u),rows(s,u)); return
        if a=="m": await send(e.bot,c,u,s.summary(u),rows(s,u)); return
        if a=="b": await send(e.bot,c,u,s.handle(u,"назад"),rows(s,u)); return
        if a=="s": await send(e.bot,c,u,s.handle(u,"далее"),rows(s,u)); return
        if a=="a" and len(p)==3: await send(e.bot,c,u,s.answer_by_numbers(u,[int(p[2])+1]),rows(s,u)); return
        if a=="t" and len(p)==3: s.toggle(u,int(p[1]),int(p[2])); await send(e.bot,c,u,s.question_text(u),rows(s,u)); return
        if a=="d" and len(p)==2:
            picked=s.picked(u,int(p[1])); await send(e.bot,c,u,s.answer_by_numbers(u,[i+1 for i in picked]) if picked else s.handle(u,"далее"),rows(s,u)); return
    return dp

async def main():
    import legacy_max_bot as old
    from maxapi import Bot
    bot=Bot(old.read_token()); s=LaptopSurvey(list_options=False); dp=build_dispatcher(s)
    try: await dp.start_polling(bot)
    finally:
        close=getattr(bot,"close_session",None)
        if close:
            result=close()
            if asyncio.iscoroutine(result): await result
