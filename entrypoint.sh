#!/bin/bash
set -e

mkdir -p /root/.ccconvert

# Only generate config if it doesn't exist (e.g., when using volume mount)
if [ ! -f /root/.ccconvert/config.toml ]; then
    cat > /root/.ccconvert/config.toml << CONFIG
# Auto-generated from environment variables
openai_api_key = "${CCCONVERT_OPENAI_API_KEY:-}"
openai_base_url = "${CCCONVERT_OPENAI_BASE_URL:-https://api.openai.com/v1}"
host = "${CCCONVERT_HOST:-0.0.0.0}"
port = ${CCCONVERT_PORT:-8080}
api_key = "${CCCONVERT_API_KEY:-}"
log_level = "${CCCONVERT_LOG_LEVEL:-INFO}"
tavily_api_key = "${CCCONVERT_TAVILY_API_KEY:-}"
tavily_timeout = ${CCCONVERT_TAVILY_TIMEOUT:-30.0}
tavily_max_results = ${CCCONVERT_TAVILY_MAX_RESULTS:-5}

# TongXiao (通晓) Web Search Configuration
tongxiao_api_key = "${CCCONVERT_TONGXIAO_API_KEY:-}"
tongxiao_timeout = ${CCCONVERT_TONGXIAO_TIMEOUT:-30.0}
tongxiao_max_results = ${CCCONVERT_TONGXIAO_MAX_RESULTS:-5}

# Web Search Provider: tavily, tongxiao, or both
websearch_provider = "${CCCONVERT_WEBSEARCH_PROVIDER:-tavily}"
websearch_max_uses = ${CCCONVERT_WEBSEARCH_MAX_USES:-5}
cors_origins = ["*"]
cors_credentials = true
cors_methods = ["*"]
cors_headers = ["*"]
CONFIG
fi

# Execute the command passed to container, or default to ccconvert (foreground mode)
exec "${@:-ccconvert}"
