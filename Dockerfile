# --------------------
# Base image
# --------------------
FROM python:3.12-slim

# --------------------
# Set working directory
# --------------------
WORKDIR /app

# --------------------
# Copy source files
# --------------------
COPY main.py /app/main.py
COPY route_manager.py /app/route_manager.py
COPY discord_bot /app/discord_bot
COPY ignition_subscriber /app/ignition_subscriber
COPY traffic_utils.py /app/traffic_utils.py
COPY migrations.py /app/migrations.py
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

RUN printf '#!/bin/sh\npython3 /app/ignition_subscriber/test_ignition.py\n' > /usr/local/bin/test_ignition \
    && chmod +x /usr/local/bin/test_ignition

# --------------------
# Expose ports if needed
# --------------------
EXPOSE 80

# --------------------
# Default command (can be overridden in Compose)
# --------------------
CMD ["python", "-u", "main.py"]