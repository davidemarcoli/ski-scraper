FROM python:3.13-slim

# Install poetry
RUN pip install poetry

# Copy poetry files
WORKDIR /app
COPY pyproject.toml poetry.lock ./

# Configure poetry to not create a virtual environment in the container
RUN poetry config virtualenvs.create false

# Install dependencies
RUN poetry install --no-dev --no-interaction --no-ansi

# Copy application code
COPY ski_scraper ./ski_scraper

# Run the application
CMD ["poetry", "run", "uvicorn", "ski_scraper.api:app", "--host", "0.0.0.0", "--port", "8000"]
