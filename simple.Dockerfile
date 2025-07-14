FROM python:3.11-slim

WORKDIR /app

RUN pip install fastapi uvicorn

COPY simple_app.py .

CMD ["uvicorn", "simple_app:app", "--host", "0.0.0.0", "--port", "8000"]
