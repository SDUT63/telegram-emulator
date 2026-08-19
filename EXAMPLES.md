# Примеры использования Chatbot Survey

## Локальное тестирование

### 1. Запуск бота в CLI режиме

```bash
python chatbot_survey.py
```

Output:
```
Добро пожаловать в систему опроса СДУТ!

Это быстрый опросник, который поможет нам лучше понять ваши потребности.

Нажмите 'Начать' для начала опроса.

--- Тестовый запуск ---

Q: Как вас зовут?
A: Иван Петров

Q: Укажите ваш номер телефона:
A: +7 (495) 123-45-67

Q: Укажите ваш email:
A: ivan@example.com

Q: Ваша организация / место работы:
A: ООО Здоровье

Q: Тип вашего запроса:
A: Консультация

Q: Опишите вашу проблему или вопрос:
A: Нужна информация по долговременному уходу

--- Результаты опроса ---

Ваши ответы:

• Как вас зовут?
  Иван Петров

• Укажите ваш номер телефона:
  +7 (495) 123-45-67

• Укажите ваш email:
  ivan@example.com

• Ваша организация / место работы:
  ООО Здоровье

• Тип вашего запроса:
  Консультация

• Опишите вашу проблему или вопрос:
  Нужна информация по долговременному уходу
```

## Интеграция с MAX API

### 1. Запуск сервера

```bash
pip install -r requirements.txt
export MAX_ACCESS_TOKEN=your_token
export MAX_SKILL_ID=your_skill_id
export ADMIN_TOKEN=your_admin_token
python max_bot_integration.py
```

Server запустится на http://localhost:5000

### 2. Тестирование webhook'а с curl

#### Начать опрос

```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "session": {"user_id": "user_123"},
    "request": {"original_utterance": "Начать"}
  }'
```

Response:
```json
{
  "version": "1.0",
  "session": {},
  "response": {
    "text": "Добро пожаловать в систему опроса СДУТ!\n\nЭто быстрый опросник, который поможет нам лучше понять ваши потребности.\n\nНажмите 'Начать' для начала опроса.",
    "end_session": false
  }
}
```

#### Ответить на первый вопрос

```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "session": {"user_id": "user_123"},
    "request": {"original_utterance": "Иван Петров"}
  }'
```

Response:
```json
{
  "version": "1.0",
  "session": {},
  "response": {
    "text": "[1/6] Как вас зовут?",
    "end_session": false
  }
}
```

#### Неверный ответ (email без @)

```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "session": {"user_id": "user_123"},
    "request": {"original_utterance": "ivan.example.com"}
  }'
```

Response:
```json
{
  "version": "1.0",
  "session": {},
  "response": {
    "text": "Пожалуйста, введите корректный email адрес",
    "end_session": false
  }
}
```

#### Выбор из предложенных вариантов

```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "session": {"user_id": "user_123"},
    "request": {"original_utterance": "Консультация"}
  }'
```

Response:
```json
{
  "version": "1.0",
  "session": {},
  "response": {
    "text": "[5/6] Тип вашего запроса:",
    "buttons": [
      {"title": "Консультация", "hide": true},
      {"title": "Обучение", "hide": true},
      {"title": "Партнерство", "hide": true},
      {"title": "Другое", "hide": true}
    ],
    "end_session": false
  }
}
```

### 3. Проверка статуса

```bash
curl http://localhost:5000/health
```

Response:
```json
{"status": "ok"}
```

### 4. Экспорт результатов

```bash
curl -H "Authorization: Bearer your_admin_token" \
  http://localhost:5000/export
```

Response:
```json
{
  "export": "=== Экспорт ответов ===\n\nПользователь ID: user_123\n...",
  "count": 1
}
```

## Использование в Telegram

Если вы хотите использовать этого бота в Telegram, вам нужно создать обработчик:

```python
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    
    # Используем функцию из max_bot_integration.py
    handler, _ = create_max_handler()
    
    result = handler({
        "user_id": user_id,
        "text": text
    })
    
    if "quick_reply" in result:
        # Отправляем кнопки
        await update.message.reply_text(
            result["text"],
            reply_markup=ReplyKeyboardMarkup(
                [result["quick_reply"]],
                one_time_keyboard=True
            )
        )
    else:
        await update.message.reply_text(result["text"])

app = Application.builder().token("YOUR_BOT_TOKEN").build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()
```

## Развертывание на Heroku

```bash
# 1. Инициализируем git (если еще не сделано)
git init
git add .
git commit -m "Initial commit: Survey bot"

# 2. Создаем Heroku приложение
heroku create your-app-name

# 3. Устанавливаем переменные окружения
heroku config:set MAX_ACCESS_TOKEN=your_token
heroku config:set MAX_SKILL_ID=your_skill_id
heroku config:set ADMIN_TOKEN=your_admin_token

# 4. Делаем push
git push heroku main

# 5. Проверяем логи
heroku logs --tail
```

## Развертывание на Docker

```bash
# 1. Создаем .env файл
cp .env.example .env
# Редактируем .env

# 2. Запускаем с docker-compose
docker-compose up -d

# 3. Проверяем статус
docker-compose ps

# 4. Просматриваем логи
docker-compose logs -f survey-bot

# 5. Останавливаем сервис
docker-compose down
```

## Интеграция с Google Sheets

Вы можете отправлять результаты в Google Sheets:

```python
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
import gspread

def save_to_sheets(responses):
    creds = Credentials.from_service_account_file('credentials.json')
    client = gspread.authorize(creds)
    
    sheet = client.open('Survey Results').sheet1
    
    for user_id, data in responses.items():
        row = [user_id, data['timestamp']]
        for q in questions:
            row.append(data['answers'].get(q['id'], ''))
        sheet.append_row(row)
```

## Интеграция с Slack

Отправляйте уведомления о новых ответах в Slack:

```python
import requests

def notify_slack(user_data):
    webhook_url = os.getenv('SLACK_WEBHOOK_URL')
    
    message = {
        "text": "Новый ответ на опрос!",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Имя:* {user_data['answers']['name']}"}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Email:* {user_data['answers']['email']}"}
            }
        ]
    }
    
    requests.post(webhook_url, json=message)
```

## Performance Testing

```bash
# Установляем Apache Bench
# На macOS: brew install httpd
# На Linux: sudo apt-get install apache2-utils

# Запускаем тест с 100 запросами, 10 одновременно
ab -n 100 -c 10 http://localhost:5000/health

# Результаты покажут время ответа и пропускную способность
```

## Мониторинг

Включите логирование и мониторинг:

```bash
# Запуск с логированием в файл
python max_bot_integration.py > bot.log 2>&1 &

# Мониторинг логов в реальном времени
tail -f bot.log

# Поиск ошибок в логах
grep ERROR bot.log
```

## Troubleshooting

### Проблема: "ModuleNotFoundError: No module named 'flask'"

**Решение:**
```bash
pip install -r requirements.txt
```

### Проблема: "Address already in use"

**Решение:**
```bash
# Найти процесс, который использует порт 5000
lsof -i :5000

# Убить процесс
kill -9 <PID>

# Или использовать другой порт
PORT=8000 python max_bot_integration.py
```

### Проблема: "Invalid email format"

**Решение:**
Убедитесь, что email содержит символ `@` и точку (например: test@example.com)

### Проблема: "Phone validation failed"

**Решение:**
Введите номер телефона с минимум 10 цифрами (код страны + номер)
