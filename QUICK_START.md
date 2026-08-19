# Быстрый старт - СДУТ Chatbot Survey

## За 5 минут к работающему боту! 🚀

### Требования
- Python 3.7+
- pip

### Шаг 1: Установка зависимостей

```bash
pip install -r requirements.txt
```

### Шаг 2: Запуск локального теста

```bash
python chatbot_survey.py
```

Вы увидите пример взаимодействия с ботом и сохраненные результаты.

### Шаг 3: Запуск сервера MAX

```bash
# Скопируйте пример конфигурации
cp .env.example .env

# Отредактируйте .env с вашими токенами
# MAX_ACCESS_TOKEN=your_token_here
# MAX_SKILL_ID=your_skill_id_here

# Запустите сервер
python max_bot_integration.py
```

Сервер будет доступен на `http://localhost:5000`

### Шаг 4: Тестирование webhook'а

```bash
# В другом терминале
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "session": {"user_id": "test123"},
    "request": {"original_utterance": "Начать"}
  }'
```

Вы должны получить JSON ответ с первым вопросом.

## Использование скрипта start.sh

```bash
./start.sh
```

Скрипт предложит вам выбрать, что вы хотите сделать:
1. Запустить сервер
2. Запустить локальный тест
3. Запустить юнит-тесты
4. Экспортировать результаты
5. Выход

## Файлы проекта

| Файл | Назначение |
|------|-----------|
| `chatbot_survey.py` | Основной класс SurveyBot |
| `max_bot_integration.py` | Flask сервер для MAX API |
| `requirements.txt` | Python зависимости |
| `config.json` | Конфигурация бота |
| `.env.example` | Пример переменных окружения |
| `survey_responses.json` | Сохраненные ответы (автоматически) |
| `CHATBOT_SURVEY_README.md` | Подробная документация |
| `EXAMPLES.md` | Примеры использования |
| `test_survey.py` | Юнит-тесты |
| `docker-compose.yml` | Docker конфигурация |
| `Dockerfile` | Docker образ |

## Следующие шаги

1. **Настройте вопросы опросника** - отредактируйте `chatbot_survey.py`
2. **Интегрируйте с MAX** - используйте webhook URL в консоли MAX
3. **Экспортируйте результаты** - используйте GET /export endpoint
4. **Разверните на сервере** - используйте Docker или Heroku

## Решение проблем

### "ModuleNotFoundError: No module named 'flask'"

```bash
pip install -r requirements.txt
```

### "Address already in use"

```bash
# Найти и убить процесс на порту 5000
lsof -i :5000
kill -9 <PID>

# Или запустить на другом порту
PORT=8000 python max_bot_integration.py
```

### Бот не отвечает на сообщения

1. Проверьте, запущен ли сервер: `curl http://localhost:5000/health`
2. Проверьте логи в консоли
3. Убедитесь, что .env содержит правильные токены

## Получить помощь

- 📖 [Полная документация](CHATBOT_SURVEY_README.md)
- 💡 [Примеры использования](EXAMPLES.md)
- 🧪 [Запустить тесты](test_survey.py)

---

**Успеха с вашим опросником СДУТ! 🎉**
