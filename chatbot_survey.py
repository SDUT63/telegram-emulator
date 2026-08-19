#!/usr/bin/env python3
"""
Simple Chatbot Survey for MAX (Yandex API)
Collects user information through an interactive questionnaire
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Any
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SurveyBot:
    """Interactive survey bot for collecting user information"""
    
    def __init__(self):
        self.responses_file = "survey_responses.json"
        self.load_responses()
        
        # Define survey questions
        self.questions = [
            {
                "id": "name",
                "text": "Как вас зовут?",
                "type": "text",
                "required": True
            },
            {
                "id": "phone",
                "text": "Укажите ваш номер телефона:",
                "type": "phone",
                "required": True
            },
            {
                "id": "email",
                "text": "Укажите ваш email:",
                "type": "email",
                "required": True
            },
            {
                "id": "organization",
                "text": "Ваша организация / место работы:",
                "type": "text",
                "required": False
            },
            {
                "id": "inquiry_type",
                "text": "Тип вашего запроса:",
                "type": "choice",
                "options": [
                    "Консультация",
                    "Обучение",
                    "Партнерство",
                    "Другое"
                ],
                "required": True
            },
            {
                "id": "message",
                "text": "Опишите вашу проблему или вопрос:",
                "type": "text",
                "required": True
            }
        ]
        
        self.current_question_index = {}
    
    def load_responses(self):
        """Load existing responses from file"""
        if os.path.exists(self.responses_file):
            with open(self.responses_file, 'r', encoding='utf-8') as f:
                self.responses = json.load(f)
        else:
            self.responses = {}
    
    def save_responses(self):
        """Save responses to file"""
        with open(self.responses_file, 'w', encoding='utf-8') as f:
            json.dump(self.responses, f, ensure_ascii=False, indent=2)
    
    def get_start_message(self) -> str:
        """Get the initial greeting message"""
        return """Добро пожаловать в систему опроса СДУТ!
        
Это быстрый опросник, который поможет нам лучше понять ваши потребности.

Нажмите 'Начать' для начала опроса."""
    
    def get_next_question(self, user_id: str) -> Dict[str, Any]:
        """Get the next question for user"""
        if user_id not in self.current_question_index:
            self.current_question_index[user_id] = 0
            if user_id not in self.responses:
                self.responses[user_id] = {
                    "timestamp": datetime.now().isoformat(),
                    "answers": {}
                }
        
        idx = self.current_question_index[user_id]
        if idx >= len(self.questions):
            return {
                "type": "completed",
                "message": "Спасибо за ответы! Ваши данные сохранены."
            }
        
        return {
            "type": "question",
            "question": self.questions[idx],
            "progress": f"{idx + 1}/{len(self.questions)}"
        }
    
    def process_answer(self, user_id: str, answer: str) -> Dict[str, Any]:
        """Process user's answer"""
        if user_id not in self.current_question_index:
            return {"type": "error", "message": "Пожалуйста, начните опрос с начала"}
        
        idx = self.current_question_index[user_id]
        if idx >= len(self.questions):
            return {"type": "error", "message": "Опрос уже завершен"}
        
        question = self.questions[idx]
        
        # Validate answer
        validation = self.validate_answer(question, answer)
        if not validation["valid"]:
            return {
                "type": "error",
                "message": validation["message"]
            }
        
        # Save answer
        self.responses[user_id]["answers"][question["id"]] = answer
        self.save_responses()
        
        # Move to next question
        self.current_question_index[user_id] += 1
        
        return {
            "type": "success",
            "message": "✓ Ответ сохранен"
        }
    
    def validate_answer(self, question: Dict[str, Any], answer: str) -> Dict[str, Any]:
        """Validate user's answer"""
        if not answer and question["required"]:
            return {
                "valid": False,
                "message": f"Пожалуйста, ответьте на вопрос: {question['text']}"
            }
        
        if question["type"] == "email":
            if "@" not in answer:
                return {
                    "valid": False,
                    "message": "Пожалуйста, введите корректный email адрес"
                }
        
        if question["type"] == "phone":
            # Basic phone validation
            digits = ''.join(c for c in answer if c.isdigit())
            if len(digits) < 10:
                return {
                    "valid": False,
                    "message": "Пожалуйста, введите корректный номер телефона"
                }
        
        if question["type"] == "choice":
            if answer not in question.get("options", []):
                return {
                    "valid": False,
                    "message": f"Пожалуйста, выберите один из предложенных вариантов"
                }
        
        return {"valid": True}
    
    def get_survey_summary(self, user_id: str) -> str:
        """Get a summary of user's answers"""
        if user_id not in self.responses:
            return "Данные не найдены"
        
        data = self.responses[user_id]
        summary = "Ваши ответы:\n\n"
        
        for question in self.questions:
            if question["id"] in data["answers"]:
                summary += f"• {question['text']}\n  {data['answers'][question['id']]}\n\n"
        
        return summary
    
    def export_responses(self) -> str:
        """Export all responses to a formatted string"""
        if not self.responses:
            return "Нет данных для экспорта"
        
        export = "=== Экспорт ответов ===\n\n"
        
        for user_id, data in self.responses.items():
            export += f"Пользователь ID: {user_id}\n"
            export += f"Дата: {data['timestamp']}\n"
            export += "-" * 40 + "\n"
            
            for question in self.questions:
                if question["id"] in data["answers"]:
                    export += f"{question['text']}\n"
                    export += f"  → {data['answers'][question['id']]}\n"
            
            export += "\n" + "=" * 40 + "\n\n"
        
        return export


# Example usage for integration with MAX API
def create_max_handler():
    """Create a handler function for MAX API"""
    bot = SurveyBot()
    
    def handle_message(message: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming message from MAX API"""
        user_id = message.get("user_id", "unknown")
        text = message.get("text", "").strip()
        
        if text.lower() in ["начать", "start", "/start"]:
            return {
                "text": bot.get_start_message(),
                "quick_reply": ["Начать"]
            }
        
        if text.lower() in ["результаты", "summary"]:
            return {"text": bot.get_survey_summary(user_id)}
        
        if text.lower() in ["отмена", "cancel", "/cancel"]:
            bot.current_question_index[user_id] = 0
            return {"text": "Опрос отменен. Введите 'Начать' для нового опроса"}
        
        # Get next question
        next_q = bot.get_next_question(user_id)
        
        if next_q["type"] == "completed":
            bot.current_question_index[user_id] = 0
            return {"text": next_q["message"]}
        
        if next_q["type"] == "question":
            question = next_q["question"]
            response_text = f"[{next_q['progress']}] {question['text']}"
            
            if question["type"] == "choice":
                return {
                    "text": response_text,
                    "quick_reply": question["options"]
                }
            
            return {"text": response_text}
        
        # Process answer if we're in the middle of survey
        validation = bot.process_answer(user_id, text)
        
        if validation["type"] == "error":
            return {"text": validation["message"]}
        
        # Get next question
        next_q = bot.get_next_question(user_id)
        
        if next_q["type"] == "completed":
            bot.current_question_index[user_id] = 0
            return {
                "text": next_q["message"] + "\n\nОтправить новый опрос? Введите 'Начать'"
            }
        
        question = next_q["question"]
        response_text = f"[{next_q['progress']}] {question['text']}"
        
        if question["type"] == "choice":
            return {
                "text": response_text,
                "quick_reply": question["options"]
            }
        
        return {"text": response_text}
    
    return handle_message, bot


if __name__ == "__main__":
    # Simple CLI test
    bot = SurveyBot()
    
    print(bot.get_start_message())
    print("\n--- Тестовый запуск ---\n")
    
    user_id = "test_user_1"
    
    # Simulate user interaction
    test_answers = [
        "Иван Петров",
        "+7 (495) 123-45-67",
        "ivan@example.com",
        "ООО Здоровье",
        "Консультация",
        "Нужна информация по долговременному уходу"
    ]
    
    for i, answer in enumerate(test_answers):
        question = bot.get_next_question(user_id)
        print(f"Q: {question['question']['text']}")
        print(f"A: {answer}\n")
        bot.process_answer(user_id, answer)
    
    print("\n--- Результаты опроса ---\n")
    print(bot.get_survey_summary(user_id))
