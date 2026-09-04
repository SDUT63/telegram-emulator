FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY chatbot_survey.py .
COPY max_bot_integration.py .
COPY webapp/ ./webapp/

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:5000/health')"

# Run application
CMD ["python", "max_bot_integration.py"]
