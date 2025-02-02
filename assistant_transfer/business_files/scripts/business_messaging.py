import os

# Define your business signature
business_signature = "\n\n--\n[Your Business Name]\n[Your Contact Info]"

# Function to send a message
def send_message(phone_number, message):
    # Append the business signature to the message
    full_message = message + business_signature
    os.system(f"termux-sms-send -n {phone_number} '{full_message}'")
    print(f"Message sent to {phone_number}")

# List of phone numbers and messages
contacts = [
    {"number": "+14055939800", "message": "Thank you for choosing our services!"},
    {"number": "+14051234567", "message": "Your appointment is confirmed for tomorrow at 10 AM."},
]

# Send messages to all contacts
for contact in contacts:
    send_message(contact["number"], contact["message"])

