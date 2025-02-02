import os

# Define phone number and message
phone_number = "+14055939800"
message = "Hello! This is an automated message from Termux."

# Use Termux API to send SMS
os.system(f"termux-sms-send -n {phone_number} '{message}'")
