FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir fastapi uvicorn scikit-learn pandas numpy joblib

EXPOSE 8001

CMD ["uvicorn", "training.inference:app", "--host", "0.0.0.0", "--port", "8001"]
