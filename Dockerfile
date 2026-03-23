FROM python:3.10-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

EXPOSE 8000

ENTRYPOINT ["domoticz-mcp"]
CMD ["--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]