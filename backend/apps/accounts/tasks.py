from celery import shared_task
from django.contrib.auth import authenticate
from django.db import IntegrityError
from apps.accounts.models import User
from apps.core.redis_client import acquire_lock, release_lock

@shared_task
def process_login_task(email, password):
    user = authenticate(username=email, password=password)
    if user is not None:
        return {"status": "success", "user_id": str(user.id)}
    return {"status": "error", "message": "Invalid email or password."}

@shared_task
def process_registration_task(form_data):
    email = form_data.get("email", "").strip().lower()
    
    # We acquire lock inside the task to prevent duplicate celery jobs
    token = acquire_lock(f"register:{email}", ttl_ms=10000, blocking=True)
    try:
        if User.objects.filter(email=email).exists():
            return {"status": "error", "message": "An account with this email already exists."}
            
        user = User(
            email=email,
            role=form_data.get("role")
        )
        user.set_password(form_data.get("password1"))
        
        if user.role == "COMPANY":
            user.first_name = (form_data.get("company_name") or "").strip()
            user.last_name = ""
        else:
            user.first_name = (form_data.get("first_name") or "").strip()
            user.last_name = (form_data.get("last_name") or "").strip()
            
        user.save()
        return {"status": "success", "user_id": str(user.id)}
    except IntegrityError:
        return {"status": "error", "message": "An account with this email already exists."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if token:
            release_lock(f"register:{email}", token)
