FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY awaaz ./awaaz
RUN pip install --no-cache-dir .
ENV PORT=8000
CMD ["sh", "-c", "uvicorn --factory awaaz.api.app:create_app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
