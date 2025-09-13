# Base image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy script
COPY subscriber.py .

# The CMD will use environment variables from compose
CMD ["python", "subscriber.py"]