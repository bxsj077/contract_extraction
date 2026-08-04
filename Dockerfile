FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .
ENV CONTRACT_ROOT=/contracts CONTRACT_OUTPUT=/data
VOLUME ["/contracts", "/data"]
EXPOSE 8000
CMD ["uvicorn", "contract_extraction.api:app", "--host", "0.0.0.0", "--port", "8000"]
