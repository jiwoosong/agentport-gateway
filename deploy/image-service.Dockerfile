FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.162.2 uvicorn==0.54.0 pillow==13.0.0 \
    && useradd --uid 10001 --create-home converter
COPY examples/image_converter.py /app/image_converter.py
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER converter
EXPOSE 8080
CMD ["uvicorn", "image_converter:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
