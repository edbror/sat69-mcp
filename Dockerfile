FROM python:3.13-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
EXPOSE 8080
CMD ["sat69-web"]
