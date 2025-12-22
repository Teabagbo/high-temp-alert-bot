# 1. Start with a lightweight version of Python 3.11
FROM python:3.11-slim

# 2. Create a folder inside the container called 'app' to hold your files
WORKDIR /app

# 3. Copy your requirements.txt file into that folder
COPY requirements.txt .

# 4. Install the required libraries (aiogram, aiohttp, etc.)
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of your bot code (bot.py) into the folder
COPY . .

# 6. Tell the container to run your bot when it starts
CMD ["python", "bot.py"]