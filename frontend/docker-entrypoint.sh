#!/bin/sh
# Inject API_BASE_URL into config.js at container start so the static
# frontend pages reach the correct backend URL without a rebuild.
if [ -n "$API_BASE_URL" ]; then
  echo "window.API_BASE_URL = '${API_BASE_URL}';" > /usr/share/nginx/html/config.js
fi
exec nginx -g 'daemon off;'
