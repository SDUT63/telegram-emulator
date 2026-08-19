#!/usr/bin/env python3
"""
Yandex MAX API Integration for Survey Bot
Handles webhook callbacks from MAX platform
"""

import json
import os
import logging
from flask import Flask, request, jsonify
from chatbot_survey import SurveyBot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
bot = SurveyBot()


class MaxBotIntegration:
    """Integration with Yandex MAX API"""
    
    def __init__(self, bot: SurveyBot):
        self.bot = bot
        self.access_token = os.getenv("MAX_ACCESS_TOKEN", "")
        self.skill_id = os.getenv("MAX_SKILL_ID", "")
    
    def parse_max_message(self, payload: dict) -> dict:
        """Parse incoming MAX API message"""
        return {
            "user_id": payload.get("session", {}).get("user_id", "unknown"),
            "text": payload.get("request", {}).get("original_utterance", ""),
            "payload": payload
        }
    
    def format_max_response(self, text: str, quick_replies=None) -> dict:
        """Format response for MAX API"""
        response = {
            "version": "1.0",
            "session": {},
            "response": {
                "text": text,
                "end_session": False
            }
        }
        
        if quick_replies:
            response["response"]["buttons"] = [
                {"title": reply, "hide": True} for reply in quick_replies
            ]
        
        return response
    
    def handle_request(self, payload: dict) -> dict:
        """Handle incoming request from MAX"""
        try:
            message = self.parse_max_message(payload)
            user_id = message["user_id"]
            text = message["text"]
            
            logger.info(f"Message from {user_id}: {text}")
            
            # Process with survey bot
            if text.lower() in ["начать", "start"]:
                next_q = self.bot.get_next_question(user_id)
                if next_q["type"] == "question":
                    question = next_q["question"]
                    response_text = f"{question['text']}"
                    
                    if question["type"] == "choice":
                        return self.format_max_response(
                            response_text,
                            quick_replies=question["options"]
                        )
                    
                    return self.format_max_response(response_text)
            
            elif text.lower() in ["отмена", "cancel"]:
                if user_id in self.bot.current_question_index:
                    self.bot.current_question_index[user_id] = 0
                return self.format_max_response(
                    "Опрос отменен. Введите 'Начать' для нового опроса"
                )
            
            elif text.lower() in ["результаты", "summary"]:
                return self.format_max_response(self.bot.get_survey_summary(user_id))
            
            else:
                # Process answer
                validation = self.bot.process_answer(user_id, text)
                
                if validation["type"] == "error":
                    return self.format_max_response(validation["message"])
                
                # Get next question
                next_q = self.bot.get_next_question(user_id)
                
                if next_q["type"] == "completed":
                    self.bot.current_question_index[user_id] = 0
                    return self.format_max_response(
                        next_q["message"] + "\n\nОтправить новый опрос? Введите 'Начать'",
                        quick_replies=["Начать"]
                    )
                
                question = next_q["question"]
                response_text = f"[{next_q['progress']}] {question['text']}"
                
                if question["type"] == "choice":
                    return self.format_max_response(
                        response_text,
                        quick_replies=question["options"]
                    )
                
                return self.format_max_response(response_text)
        
        except Exception as e:
            logger.error(f"Error handling request: {e}")
            return self.format_max_response("Произошла ошибка. Пожалуйста, попробуйте позже.")


max_integration = MaxBotIntegration(bot)


@app.route("/webhook", methods=["POST"])
def webhook():
    """Webhook endpoint for MAX API"""
    payload = request.get_json()
    logger.info(f"Received webhook: {json.dumps(payload, ensure_ascii=False)}")
    
    response = max_integration.handle_request(payload)
    return jsonify(response)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok"})


@app.route("/export", methods=["GET"])
def export_responses():
    """Export all responses as text"""
    auth_token = request.headers.get("Authorization", "")
    if auth_token != f"Bearer {os.getenv('ADMIN_TOKEN', '')}":
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({
        "export": bot.export_responses(),
        "count": len(bot.responses)
    })


if __name__ == "__main__":
    # Run Flask app
    debug = os.getenv("DEBUG", "False") == "True"
    port = int(os.getenv("PORT", 5000))
    
    logger.info(f"Starting MAX Bot Integration on port {port}")
    app.run(debug=debug, host="0.0.0.0", port=port)
