#
# Builder Stage
#
FROM python:3.11-slim as builder

WORKDIR /app

# Install poetry
RUN pip install poetry

# Configure poetry to create the venv in the project directory
RUN poetry config virtualenvs.in-project true

# Copy dependency management files
COPY pyproject.toml poetry.lock* ./

# Install dependencies into a virtual environment
RUN poetry install --no-root --without dev

# Copy the application source code into the builder
# This is so we can copy the whole /app directory in the next stage
COPY ./app ./app
COPY test_db.py .

#
# Final Stage
#
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

# Copy the application code and the virtual environment from the builder stage
COPY --from=builder /app /app

# Set the PATH to use the virtual environment's binaries
ENV PATH="/app/.venv/bin:$PATH"

# Expose the application port
EXPOSE 5050

# Run the application
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5050"]
