#!/bin/bash
# Mac: run  ./start_bot.sh   ("caffeinate" keeps the Mac awake while the bot runs)
cd "$(dirname "$0")"
source .venv/bin/activate
caffeinate -i python run_bot.py
