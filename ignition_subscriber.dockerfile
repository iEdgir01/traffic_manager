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
COPY migrations.py /app/migrations.py
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
# Create CLI aliases
# --------------------
RUN printf '#!/bin/sh\npython3 /app/route_manager.py\n' > /usr/local/bin/menu \
    && chmod +x /usr/local/bin/menu

RUN printf '#!/bin/sh\npython3 /app/ignition_subscriber/test_ignition.py\n' > /usr/local/bin/test_ignition \
    && chmod +x /usr/local/bin/test_ignition

# --------------------
# Default command
# --------------------
CMD ["python", "-m", "ignition_subscriber.subscriber"]