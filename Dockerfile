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
COPY main.py /app/main.py
COPY route_manager.py /app/route_manager.py
COPY discord_bot /app/discord_bot
COPY ignition_subscriber /app/ignition_subscriber
COPY traffic_utils /app/traffic_utils
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
# Create a CLI alias 'menu' for route_manager.py
# --------------------
RUN echo '#!/bin/bash\npython3 /app/route_manager.py' > /usr/local/bin/menu \
    && chmod +x /usr/local/bin/menu

# --------------------
# Expose ports if needed
# --------------------
EXPOSE 80

# --------------------
# Default command when container starts
# --------------------
CMD ["python", "main.py"]