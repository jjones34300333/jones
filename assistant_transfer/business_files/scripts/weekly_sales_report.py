Paste this code:
python
from datetime import datetime, timedelta

# Define report content (example data)
today = datetime.now()
last_week = today - timedelta(days=7)

report_content = f"""
Weekly Sales Report ({last_week.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}):
Total Sales: $5,000
New Clients Acquired: 10
Follow-Ups Sent: 15
"""
Save the report in the summaries folder
report_file = f"/data/data/com.termux/files/home/business_files/summaries/{today.strftime('%Y-%m-%d')}_sales_report.txt"
with open(report_file, "w") as f:
f.write(report_content)
print(f"Weekly sales report saved to {report_file}")from datetime import datetime, timedelta

# Define report content (example data)
today = datetime.now()
last_week = today - timedelta(days=7)

report_content = f"""

