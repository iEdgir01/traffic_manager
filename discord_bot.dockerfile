# --------------------
# Base image
# --------------------
FROM python:3.12-slim

# --------------------
# Set working directory
# --------------------
WORKDIR /app

# --------------------
# Copy only necessary source files
# --------------------
COPY discord_bot /app/discord_bot
COPY traffic_utils.py /app/traffic_utils.py
COPY route_manager.py /app/route_manager.py
COPY requirements.txt /app/requirements.txt

# --------------------
# Install dependencies
# --------------------
RUN pip install --no-cache-dir -r requirements.txt

# --------------------
# Create required directories
# --------------------
RUN mkdir -p /app/data/maps

# --------------------
# Create CLI alias
# --------------------
RUN echo '#!/bin/bash\npython3 /app/route_manager.py' > /usr/local/bin/menu \
    && chmod +x /usr/local/bin/menu

# --------------------
# Default command
# --------------------
CMD ["python", "-u", "discord_bot/traffic_helper.py"]