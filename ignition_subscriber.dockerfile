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
COPY ignition_subscriber /app/ignition_subscriber
COPY discord_bot/discord_notify.py /app/discord_bot/discord_notify.py
COPY traffic_utils.py /app/traffic_utils.py
COPY route_manager.py /app/route_manager.py
COPY test_ignition.py /app/test_ignition.py
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
# Create CLI aliases
# --------------------
RUN echo '#!/bin/bash\npython3 /app/route_manager.py' > /usr/local/bin/menu \
    && chmod +x /usr/local/bin/menu

RUN echo '#!/bin/bash\npython3 /app/test_ignition.py' > /usr/local/bin/test \
    && chmod +x /usr/local/bin/test

# --------------------
# Default command
# --------------------
CMD ["python", "-m", "ignition_subscriber.subscriber"]