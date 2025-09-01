import uvicorn
import os
import sys
import django
from pathlib import Path

def setup_django():
    """Setup Django before starting the server"""
    # Add the current directory to Python path
    current_dir = Path(__file__).parent
    sys.path.insert(0, str(current_dir))
    
    # Setup Django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interview_app.settings')
    django.setup()
    
    # Run Django migrations
    from django.core.management import execute_from_command_line
    execute_from_command_line(['manage.py', 'migrate'])
    
    # Create superuser if not exists
    try:
        from django.contrib.auth.models import User
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
            print("Superuser 'admin' created. Password: admin123")
        else:
            print("Superuser already exists")
    except Exception as e:
        print(f"Error with superuser: {e}")

def main():
    """Main function to start the application"""
    print("Setting up Django...")
    setup_django()
    
    print("Starting Gemini Live Interview Application...")
    print("Access the app at: http://localhost:8000")
    print("Access Django Admin at: http://localhost:8000/django/admin/")
    print("Username: admin, Password: admin123")
    
    # Start the FastAPI server with ASGI
    uvicorn.run(
        "interview_app.asgi:application",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )

if __name__ == "__main__":
    main()