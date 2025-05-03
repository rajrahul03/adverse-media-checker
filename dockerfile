FROM python:3.10-bullseye

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libffi-dev \
    cmake \
    wkhtmltopdf \
    libssl1.1 \
    && apt-get clean

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]