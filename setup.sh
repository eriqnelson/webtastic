#!/bin/bash

# Clone the repository (replace URL as needed)
git clone https://github.com/eriqnelson/webtastic.git || echo "Repo already cloned."

# Change to project directory
cd webtastic || exit 1

# Set up Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install required packages
pip install --upgrade pip
pip install meshtastic python-dotenv

echo "Setup complete."
