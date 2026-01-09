# Use a lightweight Python base image
FROM python:3.11-slim

# Set environment variables to optimize Python for Docker
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Install system dependencies required for onnxruntime/rembg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy the project files
COPY . .

# Install the package
RUN pip install --no-cache-dir .

# Set the entrypoint to your CLI tool
ENTRYPOINT ["garmin-graphics-generator"]

# Default argument if none provided
CMD ["--about"]