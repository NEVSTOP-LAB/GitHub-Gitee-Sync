FROM python:3.11-slim

# Install git
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy sync script
COPY sync.py .

# Default entrypoint
ENTRYPOINT ["python", "sync.py"]
