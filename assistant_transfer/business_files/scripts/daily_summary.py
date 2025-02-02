from datetime import datetime

# Define the summary content (key points from today)
summary_content = """
   Key Points from Today's Conversation:
   - Set up file organization system.
   - Created messaging automation with business signature.
   - Automated daily tasks using Cron.
   """

# Get today's date and create a log file name
today = datetime.now().strftime("%Y-%m-%d")
log_file = f"/data/data/com.termux/files/home/business_files/summaries/{today}_summary.txt"

# Ensure summaries folder exists
import os
os.makedirs(os.path.dirname(log_file), exist_ok=True)

# Write summary content to log file
with open(log_file, "w") as f:
    f.write(summary_content)

print(f"Summary saved to {log_file}")

