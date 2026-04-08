import sys
import os

# Add your project directory to the path
path = '6QRMenu'
if path not in sys.path:
    sys.path.insert(0, path)

# Set environment variables
os.environ['ADMIN_PASSWORD'] = 'admin123!@#'

# Import your Flask app
from app import app as application