FROM python:3.11-slim

WORKDIR /app

# Copy source and metadata
COPY pyproject.toml app.py ./

# Install dependencies
RUN pip install --no-cache-dir -e .

# Run the API server
ENTRYPOINT ["python", "app.py"]
