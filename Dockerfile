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
COPY entrypoint.py .

# Use entrypoint.py directly as the entry point (no shell shim).
# GitHub Actions Docker container actions set inputs as INPUT_{NAME} where
# {NAME} is the uppercased input name with hyphens PRESERVED (e.g.
# INPUT_GITHUB-OWNER).  POSIX shells like dash (/bin/sh on Debian) strip
# environment variables whose names contain hyphens, so invoking Python
# through a shell wrapper would lose those variables.  By calling Python
# directly we ensure os.environ sees every INPUT_* variable.
ENTRYPOINT ["python3", "/app/entrypoint.py"]
