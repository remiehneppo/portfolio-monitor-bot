FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps for matplotlib
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libfreetype6-dev \
        libpng-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY bot/ bot/
COPY nft_holders.py .

# Ensure data directory exists
RUN mkdir -p /app/data

# Persistent volume for user data & CSV snapshots
VOLUME ["/app/data"]

CMD ["python", "-m", "bot.main"]
