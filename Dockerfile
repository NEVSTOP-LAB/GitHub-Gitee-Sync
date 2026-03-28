FROM python:3.11.12-slim

# Install git
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (main entry + lib modules)
COPY sync.py .
COPY lib/ ./lib/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Use entrypoint.sh as entry point
# - GitHub Action mode: receives INPUT_* env vars, maps them, calls sync.py
# - Docker standalone mode: uses standard env vars directly
ENTRYPOINT ["/entrypoint.sh"]
