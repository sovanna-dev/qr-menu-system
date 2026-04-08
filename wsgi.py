import sys
import os

# Add your project directory to the path
path = os.path.dirname(os.path.abspath(__file__))
if path not in sys.path:
    sys.path.insert(0, path)

# Set environment variables
os.environ['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD', 'admin123!@#')

# Import your Flask app
from app import app as application

if __name__ == "__main__":
    application.run()
