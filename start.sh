#!/bin/bash

# СДУТ Chatbot Survey Starter Script

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== СДУТ Chatbot Survey Starter ===${NC}\n"

# Check Python installation
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is not installed${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Python 3 found${NC}"
python3 --version

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
echo -e "${GREEN}Activating virtual environment...${NC}"
source venv/bin/activate

# Install dependencies
echo -e "${GREEN}Installing dependencies...${NC}"
pip install -q -r requirements.txt

# Create .env if not exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Creating .env file from .env.example${NC}"
    cp .env.example .env
    echo -e "${YELLOW}Please edit .env file with your MAX API credentials${NC}"
fi

# Create data directory
mkdir -p data

# Display options
echo -e "\n${GREEN}=== Choose an option ===${NC}\n"
echo "1) Run server (MAX API)"
echo "2) Run CLI test"
echo "3) Run tests"
echo "4) Export responses"
echo -e "5) Exit\n"

read -p "Enter your choice [1-5]: " choice

case $choice in
    1)
        echo -e "${GREEN}Starting MAX Bot Server...${NC}"
        python max_bot_integration.py
        ;;
    2)
        echo -e "${GREEN}Running CLI test...${NC}"
        python chatbot_survey.py
        ;;
    3)
        echo -e "${GREEN}Running tests...${NC}"
        python -m unittest test_survey.py -v
        ;;
    4)
        echo -e "${GREEN}Exporting responses...${NC}"
        python chatbot_survey.py | grep "=== Экспорт" -A 100
        ;;
    5)
        echo -e "${GREEN}Goodbye!${NC}"
        exit 0
        ;;
    *)
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac
