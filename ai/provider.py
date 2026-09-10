# -*- coding: utf-8 -*-
"""Единый доступ к языковым моделям. Провайдер меняется одной переменной
окружения, код приложения об этом не знает.

AI_PROVIDER = yandex | gigachat | deepseek | off

«off» — не заглушка на время отладки, а рабочий режим: если модель недоступна,
превышен бюджет или ключ не задан, бот обязан продолжать работать как раньше.
Анкета важнее подсказки.
"""
from __future__ import annotations
import json, os, time, logging
from dataclasses import dataclass, field

log = logging.getLogger("сдут-ии")

@dataclass
class Reply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    provider: str = "off"
    cost_rub: float = 0.0
    ok: bool = True
    error: str = ""

# Цены за 1000 токенов, рублей. Числа нужны для учёта расходов по гранту,
# поэтому перед запуском их надо сверить с действующим прайсом провайдера
# и при необходимости задать своими: AI_PRICE_IN и AI_PRICE_OUT.
# Расхождение с актом покажет сверка — см. лист расчётной книги.
PRICES = {
    "yandex":   {"in": 1.00, "out": 2.00, "name": "YandexGPT Pro"},
    "yandex-lite": {"in": 0.20, "out": 0.40, "name": "YandexGPT Lite"},
    "gigachat": {"in": 1.50, "out": 3.00, "name": "GigaChat Pro"},
    "deepseek": {"in": 0.0133, "out": 0.0266, "name": "DeepSeek V4-Flash"},
    "off":      {"in": 0.0, "out": 0.0, "name": "отключено"},
}

class Provider:
    """Базовый класс. Наследники реализуют только _call."""
    key_env = ""
    needs: tuple[str, ...] = ()          # что ещё нужно, кроме ключа
    def __init__(self, name: str):
        self.name = name
        self.price = dict(PRICES.get(name, PRICES["off"]))
        # Прайс меняется чаще, чем код. Своя цена важнее нашей таблицы.
        for край in ("in", "out"):
            своя = os.getenv(f"AI_PRICE_{край.upper()}", "").strip()
            if своя:
                try:
                    self.price[край] = float(своя.replace(",", "."))
                except ValueError:
                    log.warning("AI_PRICE_%s: не число, беру цену из таблицы",
                                край.upper())

    def available(self) -> bool:
        return bool(os.getenv(self.key_env, "").strip())

    def ready(self) -> tuple[bool, str]:
        """Готов ли провайдер и чего не хватает. Для диагностики и /состояние."""
        if not self.key_env:
            return False, "модель отключена"
        if not os.getenv(self.key_env, "").strip():
            return False, f"не задана переменная {self.key_env}"
        for ещё in self.needs:
            if not os.getenv(ещё, "").strip():
                return False, f"не задана переменная {ещё}"
        return True, "готов"

    def ask(self, system: str, user: str, *, max_tokens: int = 600,
            timeout: float = 12.0) -> Reply:
        if not self.available():
            return Reply("", provider=self.name, ok=False, error="ключ не задан")
        started = time.time()
        try:
            text, tin, tout = self._call(system, user, max_tokens, timeout)
        except Exception as e:                                  # noqa: BLE001
            log.warning("модель %s не ответила: %s", self.name, type(e).__name__)
            return Reply("", provider=self.name, ok=False, error=str(e)[:120])
        cost = (tin * self.price["in"] + tout * self.price["out"]) / 1000
        log.info("модель %s: %d→%d токенов, %.3f ₽, %.1f с",
                 self.name, tin, tout, cost, time.time() - started)
        return Reply(text, tin, tout, self.name, cost)

    def _call(self, system, user, max_tokens, timeout):
        raise NotImplementedError


class Off(Provider):
    def available(self): return False


class Yandex(Provider):
    """Yandex Cloud Foundation Models. Данные остаются в России,
    договор с юрлицом, акты и счета-фактуры."""
    key_env = "YANDEX_API_KEY"
    needs = ("YANDEX_FOLDER_ID",)

    def _call(self, system, user, max_tokens, timeout):
        import urllib.request
        folder = os.environ["YANDEX_FOLDER_ID"]
        # Lite дешевле Pro в пять раз и для справочных ответов достаточен.
        # Имя модели можно задать своим: у Yandex они время от времени меняются.
        по_умолчанию = "yandexgpt-lite/latest" if self.name.endswith("lite") else "yandexgpt/latest"
        model = os.getenv("YANDEX_MODEL", по_умолчанию)
        body = json.dumps({
            "modelUri": f"gpt://{folder}/{model}",
            "completionOptions": {"temperature": 0.2, "maxTokens": str(max_tokens)},
            "messages": [{"role": "system", "text": system},
                         {"role": "user", "text": user}],
        }).encode()
        req = urllib.request.Request(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            data=body, headers={
                "Authorization": f"Api-Key {os.environ['YANDEX_API_KEY']}",
                "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        alt = data["result"]["alternatives"][0]["message"]["text"]
        u = data["result"].get("usage", {})
        return alt, int(u.get("inputTextTokens", 0)), int(u.get("completionTokens", 0))


class GigaChat(Provider):
    """Сбер. Данные в России. Требует получения токена по OAuth."""
    key_env = "GIGACHAT_AUTH_KEY"
    _token = ("", 0.0)
    def _auth(self, timeout):
        import urllib.request, uuid
        if self._token[0] and self._token[1] > time.time() + 60:
            return self._token[0]
        req = urllib.request.Request(
            "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            data=b"scope=GIGACHAT_API_CORP",
            headers={"Authorization": f"Basic {os.environ['GIGACHAT_AUTH_KEY']}",
                     "RqUID": str(uuid.uuid4()),
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        GigaChat._token = (d["access_token"], d["expires_at"] / 1000)
        return self._token[0]

    def _call(self, system, user, max_tokens, timeout):
        import urllib.request
        token = self._auth(timeout)
        body = json.dumps({
            "model": os.getenv("GIGACHAT_MODEL", "GigaChat-Pro"),
            "temperature": 0.2, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(
            "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
            data=body, headers={"Authorization": f"Bearer {token}",
                                "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        u = d.get("usage", {})
        return (d["choices"][0]["message"]["content"],
                int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)))


class DeepSeek(Provider):
    """Зарубежный провайдер. Использовать только там, где персональных данных
    нет вовсе, и только после уведомления Роскомнадзора о трансграничной
    передаче — см. ст. 12 Федерального закона № 152-ФЗ."""
    key_env = "DEEPSEEK_API_KEY"
    def _call(self, system, user, max_tokens, timeout):
        import urllib.request
        body = json.dumps({
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            "temperature": 0.2, "max_tokens": max_tokens, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions", data=body,
            headers={"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        u = d.get("usage", {})
        return (d["choices"][0]["message"]["content"],
                int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)))


CLASSES = {"yandex": Yandex, "yandex-lite": Yandex, "gigachat": GigaChat,
           "deepseek": DeepSeek, "off": Off}

def get(name: str | None = None) -> Provider:
    name = (name or os.getenv("AI_PROVIDER", "off")).strip().lower()
    return CLASSES.get(name, Off)(name if name in CLASSES else "off")
