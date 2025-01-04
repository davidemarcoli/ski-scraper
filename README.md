# Ski Competition Scraper API

API service that scrapes ski competition data and provides it in a structured format.

## Setup

1. Install Poetry
2. Run `poetry install`
3. Run `poetry shell`

## Development

```bash
poetry run uvicorn ski_scraper.api:app --reload
```

## Testing

```bash
poetry run pytest
```
