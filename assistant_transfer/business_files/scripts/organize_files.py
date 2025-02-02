import os
import shutil

# Define the directory to organize
directory_to_organize = "/data/data/com.termux/files/home/business_files"

# Define file categories and their extensions
file_categories = {
    "Documents": [".pdf", ".docx", ".txt"],
    "Images": [".jpg", ".jpeg", ".png"],
    "Scripts": [".py", ".sh"],
}

# Create folders for each category if they don't exist
for category in file_categories.keys():
    folder_path = os.path.join(directory_to_organize, category)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

# Move files into their respective folders
for file_name in os.listdir(directory_to_organize):
    file_path = os.path.join(directory_to_organize, file_name)
    
    # Skip directories
    if os.path.isdir(file_path):
        continue
    
    # Check file extension and move it to the appropriate folder
    for category, extensions in file_categories.items():
        if any(file_name.endswith(ext) for ext in extensions):
            destination_folder = os.path.join(directory_to_organize, category)
            shutil.move(file_path, destination_folder)
            print(f"Moved {file_name} to {destination_folder}")
            break

print("File organization complete!")

