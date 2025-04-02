#!/bin/bash

git pull

# Define the service name and path to the .plist file
SERVICE_NAME="semantic_search.service"
PLIST_PATH="$HOME/Library/LaunchAgents/com.${SERVICE_NAME}.plist"

# Check if the .plist file exists
if [ ! -f "$PLIST_PATH" ]; then
    echo "Error: The .plist file does not exist at $PLIST_PATH"
    echo "Please update the PLIST_PATH variable in the script with the correct location."
    exit 1
fi

# Unload (stop) the service
echo "Stopping the $SERVICE_NAME service..."
launchctl unload "$PLIST_PATH"
if [ $? -eq 0 ]; then
    echo "Service stopped successfully."
else
    echo "Failed to stop the service."
    exit 1
fi

# Load (start) the service
echo "Starting the $SERVICE_NAME service..."
launchctl load "$PLIST_PATH"
if [ $? -eq 0 ]; then
    echo "Service started successfully."
else
    echo "Failed to start the service."
    exit 1
fi

# Verify the service is running
echo "Checking service status..."
sleep 1  # Small delay to allow the service to start
launchctl list | grep "$SERVICE_NAME" > /dev/null
if [ $? -eq 0 ]; then
    echo "$SERVICE_NAME is running."
else
    echo "Warning: $SERVICE_NAME may not have started correctly."
fi

exit 0