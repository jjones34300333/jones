#!/usr/bin/env python3
import os
import json
from datetime import datetime, time

class SMSAutomation:
    def __init__(self, contacts_file='~/business_files/configs/contacts.json'):
        self.contacts_file = os.path.expanduser(contacts_file)
        self.work_hours_file = os.path.expanduser('~/business_files/configs/work_hours.json')
        self.load_contacts()
        self.load_work_hours()

    def load_contacts(self):
        try:
            with open(self.contacts_file, 'r') as f:
                self.contacts = json.load(f)
        except FileNotFoundError:
            self.contacts = []
            self.save_contacts()

    def save_contacts(self):
        with open(self.contacts_file, 'w') as f:
            json.dump(self.contacts, f, indent=4)

    def load_work_hours(self):
        try:
            with open(self.work_hours_file, 'r') as f:
                self.work_hours = json.load(f)
        except FileNotFoundError:
            self.work_hours = {
                "work_mode": True,
                "start_time": "09:00",
                "end_time": "17:00"
            }
            self.save_work_hours()

    def save_work_hours(self):
        with open(self.work_hours_file, 'w') as f:
            json.dump(self.work_hours, f, indent=4)

    def is_work_hours(self):
        if not self.work_hours['work_mode']:
            return False
        
        current_time = datetime.now().time()
        start_time = datetime.strptime(self.work_hours['start_time'], '%H:%M').time()
        end_time = datetime.strptime(self.work_hours['end_time'], '%H:%M').time()
        
        return start_time <= current_time <= end_time

    def add_contact(self, number, name, priority='normal'):
        contact = {
            "number": number,
            "name": name,
            "priority": priority
        }
        self.contacts.append(contact)
        self.save_contacts()
        print(f"✅ Contact {name} added successfully")

    def remove_contact(self, number):
        self.contacts = [contact for contact in self.contacts if contact['number'] != number]
        self.save_contacts()
        print(f"❌ Contact removed: {number}")

    def toggle_work_mode(self, mode=None):
        if mode is not None:
            self.work_hours['work_mode'] = mode
        else:
            self.work_hours['work_mode'] = not self.work_hours['work_mode']
        
        status = "Enabled" if self.work_hours['work_mode'] else "Disabled"
        print(f"🔄 Work Mode {status}")
        self.save_work_hours()

    def send_sms(self, number, message, priority='normal'):
        if not self.is_work_hours() and priority != 'urgent':
            print(f"❌ Outside work hours. Message to {number} not sent.")
            return False

        try:
            os.system(f"termux-sms-send -n {number} '{message}'")
            print(f"✅ SMS sent to {number}")
            return True
        except Exception as e:
            print(f"❌ Failed to send SMS to {number}: {e}")
            return False

    def send_batch_messages(self, messages):
        for msg in messages:
            self.send_sms(
                number=msg['number'], 
                message=msg['message'], 
                priority=msg.get('priority', 'normal')
            )

def main():
    sms_system = SMSAutomation()
    
    # Example usage
    sms_system.add_contact("+14055939800", "Client 1", "high")
    sms_system.add_contact("+14051234567", "Client 2", "normal")
    
    # Toggle work mode
    sms_system.toggle_work_mode()
    
    # Send messages
    messages = [
        {"number": "+14055939800", "message": "High priority update", "priority": "urgent"},
        {"number": "+14051234567", "message": "Normal business communication"}
    ]
    sms_system.send_batch_messages(messages)

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
import os

# Easy-to-modify contact list for AI interaction
contacts = [
    {"number": "+14055939800", "message": "Service update"},
    {"number": "+14051234567", "message": "Quick check-in"}
]

def send_sms(number, message):
    """
    Simple SMS sending function
    Designed for easy AI modification and interaction
    """
    try:
        os.system(f"termux-sms-send -n {number} '{message}'")
        print(f"✅ SMS sent to {number}")
        return True
    except Exception as e:
        print(f"❌ Failed to send SMS to {number}: {e}")
        return False

def main():
    """
    Primary function for sending messages
    Easily expandable for AI-driven logic
    """
    for contact in contacts:
        send_sms(contact['number'], contact['message'])

if __name__ == "__main__":
    main()

