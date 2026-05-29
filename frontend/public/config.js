// Runtime API URL — overwritten at container start by docker-entrypoint.sh
// Set API_BASE_URL env var in docker-compose to point at the backend.
window.API_BASE_URL = 'http://localhost:8000';
