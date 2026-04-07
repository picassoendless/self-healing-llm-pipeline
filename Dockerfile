FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir garak

# Copy source code
COPY . .

# Create results directory
RUN mkdir -p results

# Default command runs the full pipeline
CMD ["python", "run.py", "--config", "config/pipeline.yaml"]
