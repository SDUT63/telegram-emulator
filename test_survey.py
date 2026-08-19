#!/usr/bin/env python3
"""
Unit tests for Survey Bot
"""

import unittest
import json
import os
from chatbot_survey import SurveyBot


class TestSurveyBot(unittest.TestCase):
    """Test cases for SurveyBot class"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.bot = SurveyBot()
        self.test_user = "test_user"
    
    def tearDown(self):
        """Clean up after tests"""
        if os.path.exists("survey_responses.json"):
            os.remove("survey_responses.json")
    
    def test_get_start_message(self):
        """Test initial message"""
        msg = self.bot.get_start_message()
        self.assertIn("СДУТ", msg)
        self.assertIn("опросник", msg)
    
    def test_get_first_question(self):
        """Test getting first question"""
        question = self.bot.get_next_question(self.test_user)
        self.assertEqual(question["type"], "question")
        self.assertIn("Как вас зовут?", question["question"]["text"])
        self.assertEqual(question["progress"], "1/6")
    
    def test_validate_email(self):
        """Test email validation"""
        email_question = {
            "id": "email",
            "type": "email",
            "text": "Email?",
            "required": True
        }
        
        # Valid email
        result = self.bot.validate_answer(email_question, "test@example.com")
        self.assertTrue(result["valid"])
        
        # Invalid email
        result = self.bot.validate_answer(email_question, "test.example.com")
        self.assertFalse(result["valid"])
    
    def test_validate_phone(self):
        """Test phone validation"""
        phone_question = {
            "id": "phone",
            "type": "phone",
            "text": "Phone?",
            "required": True
        }
        
        # Valid phone
        result = self.bot.validate_answer(phone_question, "+7 (495) 123-45-67")
        self.assertTrue(result["valid"])
        
        # Invalid phone (too short)
        result = self.bot.validate_answer(phone_question, "123")
        self.assertFalse(result["valid"])
    
    def test_process_answer_sequence(self):
        """Test complete answer sequence"""
        answers = [
            "Иван Петров",
            "+7-495-123-45-67",
            "ivan@example.com",
            "ООО Здоровье",
            "Консультация",
            "Нужна информация"
        ]
        
        for answer in answers:
            # Get question
            question = self.bot.get_next_question(self.test_user)
            self.assertEqual(question["type"], "question")
            
            # Process answer
            result = self.bot.process_answer(self.test_user, answer)
            self.assertEqual(result["type"], "success")
        
        # Check final question
        question = self.bot.get_next_question(self.test_user)
        self.assertEqual(question["type"], "completed")
    
    def test_save_and_load_responses(self):
        """Test saving and loading responses"""
        self.bot.responses[self.test_user] = {
            "timestamp": "2026-08-19T12:00:00",
            "answers": {"name": "Тестовый пользователь"}
        }
        
        self.bot.save_responses()
        
        # Create new instance and load
        bot2 = SurveyBot()
        self.assertIn(self.test_user, bot2.responses)
        self.assertEqual(
            bot2.responses[self.test_user]["answers"]["name"],
            "Тестовый пользователь"
        )
    
    def test_export_responses(self):
        """Test exporting responses"""
        # Add some test data
        self.bot.responses[self.test_user] = {
            "timestamp": "2026-08-19T12:00:00",
            "answers": {
                "name": "Иван Петров",
                "phone": "+7 (495) 123-45-67",
                "email": "ivan@example.com"
            }
        }
        
        export = self.bot.export_responses()
        self.assertIn("Иван Петров", export)
        self.assertIn("+7 (495) 123-45-67", export)
        self.assertIn("ivan@example.com", export)
    
    def test_get_survey_summary(self):
        """Test getting survey summary"""
        self.bot.responses[self.test_user] = {
            "timestamp": "2026-08-19T12:00:00",
            "answers": {
                "name": "Тест Пользователь",
                "phone": "+7-999-888-7777",
                "email": "test@example.com"
            }
        }
        
        summary = self.bot.get_survey_summary(self.test_user)
        self.assertIn("Тест Пользователь", summary)
        self.assertIn("+7-999-888-7777", summary)
    
    def test_validation_choice_answer(self):
        """Test choice field validation"""
        choice_question = {
            "id": "type",
            "type": "choice",
            "text": "Choose?",
            "options": ["Опция 1", "Опция 2"],
            "required": True
        }
        
        # Valid choice
        result = self.bot.validate_answer(choice_question, "Опция 1")
        self.assertTrue(result["valid"])
        
        # Invalid choice
        result = self.bot.validate_answer(choice_question, "Опция 3")
        self.assertFalse(result["valid"])
    
    def test_required_field_validation(self):
        """Test required field validation"""
        required_question = {
            "id": "name",
            "type": "text",
            "text": "Name?",
            "required": True
        }
        
        # Empty answer
        result = self.bot.validate_answer(required_question, "")
        self.assertFalse(result["valid"])
    
    def test_optional_field(self):
        """Test optional field"""
        optional_question = {
            "id": "org",
            "type": "text",
            "text": "Organization?",
            "required": False
        }
        
        # Empty answer should be valid for optional
        result = self.bot.validate_answer(optional_question, "")
        self.assertTrue(result["valid"])


class TestMaxBotIntegration(unittest.TestCase):
    """Test cases for MAX Bot Integration"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Import Flask app after bot is created
        from max_bot_integration import app
        self.client = app.test_client()
    
    def test_health_endpoint(self):
        """Test health check endpoint"""
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'ok')
    
    def test_webhook_start_command(self):
        """Test webhook with start command"""
        response = self.client.post(
            '/webhook',
            data=json.dumps({
                'session': {'user_id': 'test_user'},
                'request': {'original_utterance': 'начать'}
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['version'], '1.0')
        self.assertIn('response', data)


if __name__ == '__main__':
    unittest.main()
