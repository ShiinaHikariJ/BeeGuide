FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app/backend
COPY backend/requirements.lock.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY backend/ /app/backend/
COPY knowledge/ /app/knowledge/
COPY --from=frontend /frontend/dist /app/frontend/dist
ENV BEEGUIDE_DATA_DIR=/app/data
EXPOSE 8008
CMD ["python", "-m", "app.serve"]
