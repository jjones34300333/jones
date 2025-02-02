#!/usr/bin/env python3
import os
from datetime import datetime, timedelta

# Define follow-up messages and schedule
follow_ups = [
    {"number": "+14055939800", "message": "We hope you're satisfied with our service! Let us know if you need anything.", "days_after": 3},
    {"number": "+14051234567", "message": "Just checking in! Do you have any questions about your recent purchase?", "days_after": 7},
]

# Get today's date
today = datetime.now()

# Send follow-up messages based on the schedule
for client in follow_ups:
    follow_up_date = today - timedelta(days=client["days_after"])
    print(f"Sending follow-up to {client['number']} sent on {follow_up_date.strftime('%Y-%m-%d')}")
    
    # Use os.system for SMS sending
    os.system(f"termux-sms-send -n {client['number']} '{client['message']}'")
import os
from datetime import datetime, timedelta

# Define follow-up messages and schedule
follow_ups = [
    {"number": "+14055939800", "message": "We hope you're satisfied with our service! Let us know if you need anything.", "days_after": 3},
    {"number": "+14051234567", "message": "Just checking in! Do you have any questions about your recent purchase?", "days_after": 7},
]

# Get today's date
today = datetime.now()

# Send follow-up messages based on the schedule
for client in follow_ups:
    follow_up_date = today - timedelta(days=client["days_after"])
    print(f"Sending follow-up to {client['number']} sent on {follow_up_date.strftime('%Y-%m-%d')}")
    os.system(f"termux-sms-send -n {client['number']} '{client['message']}'")

