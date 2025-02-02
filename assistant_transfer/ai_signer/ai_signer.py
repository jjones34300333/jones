#!/usr/bin/env python3
# Save as: ai_signer.py

import os
import json
import requests
from datetime import datetime
import sqlite3
from pathlib import Path

# Database setup
def setup_database():
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS documents
                 (id INTEGER PRIMARY KEY, 
                  title TEXT,
                  content TEXT,
                  signature TEXT,
                  timestamp TEXT)''')
    conn.commit()
    return conn

# Document handling
class DocumentManager:
    def __init__(self, db_connection):
        self.conn = db_connection
        self.cursor = self.conn.cursor()

    def save_document(self, title, content, signature):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute('''INSERT INTO documents 
                              (title, content, signature, timestamp)
                              VALUES (?, ?, ?, ?)''',
                           (title, content, signature, timestamp))
        self.conn.commit()

    def list_documents(self):
        self.cursor.execute('SELECT id, title, timestamp FROM documents')
        return self.cursor.fetchall()

    def get_document(self, doc_id):
        self.cursor.execute('''SELECT title, content, signature, timestamp 
                              FROM documents WHERE id = ?''', (doc_id,))
        return self.cursor.fetchone()

def analyze_with_ai(api_key, text):
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    
    data = {
        'model': 'gpt-4',
        'messages': [
            {'role': 'system', 'content': 'You are a helpful assistant that analyzes documents.'},
            {'role': 'user', 'content': f'Please analyze this document briefly: {text}'}
        ]
    }
    
    try:
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers=headers,
            json=data,
            timeout=30
        )
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            return f'Error: {response.status_code} - {response.text}'
    except Exception as e:
        return f'Error: {str(e)}'

def main_menu():
    print("\n=== AI Document Signer for Termux ===")
    print("1. Create new document")
    print("2. List documents")
    print("3. View document")
    print("4. Analyze document with AI")
    print("5. Exit")
    return input("Choose an option (1-5): ")

def get_api_key():
    # First check environment variable
    api_key = os.getenv('OPENAI_API_KEY')
    if api_key:
        return api_key
        
    # If not in environment, check for key file
    key_file = Path.home() / '.openai_key'
    if key_file.exists():
        return key_file.read_text().strip()
        
    # If no key found, prompt user
    api_key = input("Enter your OpenAI API key: ").strip()
    save = input("Save this key for future use? (y/n): ").lower()
    if save == 'y':
        key_file.write_text(api_key)
    return api_key

def main():
    conn = setup_database()
    doc_manager = DocumentManager(conn)
    
    while True:
        choice = main_menu()
        
        if choice == '1':
            title = input("\nEnter document title: ")
            print("Enter document content (press Ctrl+D when finished):")
            content = []
            try:
                while True:
                    line = input()
                    content.append(line)
            except EOFError:
                content = '\n'.join(content)
            
            signature = input("Enter your signature: ")
            doc_manager.save_document(title, content, signature)
            print("\nDocument saved successfully!")

        elif choice == '2':
            print("\n=== Saved Documents ===")
            for doc_id, title, timestamp in doc_manager.list_documents():
                print(f"{doc_id}. {title} ({timestamp})")

        elif choice == '3':
            doc_id = input("\nEnter document ID to view: ")
            doc = doc_manager.get_document(doc_id)
            if doc:
                title, content, signature, timestamp = doc
                print(f"\n=== {title} ===")
                print(f"Timestamp: {timestamp}")
                print("\nContent:")
                print(content)
                print(f"\nSigned by: {signature}")
            else:
                print("Document not found!")

        elif choice == '4':
            doc_id = input("\nEnter document ID to analyze: ")
            doc = doc_manager.get_document(doc_id)
            if doc:
                api_key = get_api_key()
                if api_key:
                    print("\nAnalyzing document...")
                    analysis = analyze_with_ai(api_key, doc[1])  # doc[1] is content
                    print("\nAI Analysis:")
                    print(analysis)
                else:
                    print("No API key provided!")
            else:
                print("Document not found!")

        elif choice == '5':
            print("\nGoodbye!")
            break

        input("\nPress Enter to continue...")

if __name__ == '__main__':
    main()
