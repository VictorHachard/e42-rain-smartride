#!/bin/bash

# Check for required environment variables
if [ -z "$DISCORD_WEBHOOK_URL" ]; then
  echo "Error: DISCORD_WEBHOOK_URL is not set. This variable is required."
  exit 1
fi

# Set the storage directory (volume is defined in Dockerfile)
STORAGE_DIR="/app/data"

# Ensure the storage directory exists
mkdir -p "$STORAGE_DIR"

# Build the base command with improved readability
CMD=(
  "python" "./main.py"
  "--storage-dir" "$STORAGE_DIR"
  "--webhook" "$DISCORD_WEBHOOK_URL"
  "--interval" "${INTERVAL:-300}"
)

# Add optional parameters if they are set
[ -n "$MENTION_USERS" ] && CMD+=("--mention-users" "$MENTION_USERS")
[ -n "$WEBPAGE_USER_AGENT" ] && CMD+=("--webpage-user-agent" "$WEBPAGE_USER_AGENT")

# Run the application
"${CMD[@]}"
