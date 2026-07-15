import os
import json

def get_push_subscriptions(user_id):
    """Retrieve push subscription configurations from the DB for a user."""
    try:
        from extensions import db
        from sqlalchemy import text
        # Note: We query the push_subscriptions table which must be created in production
        rows = db.session.execute(text("SELECT subscription_json FROM push_subscriptions WHERE user_id = :id"), {'id': user_id}).fetchall()
        return [json.loads(row.subscription_json) for row in rows if row.subscription_json]
    except Exception as e:
        print(f"Error fetching push subscriptions: {e}")
        return []

def send_web_push(user_id, title, body, url="/"):
    """
    Dispatch a Web Push Notification using pywebpush.
    Expects VAPID_PRIVATE_KEY and VAPID_CLAIM_EMAIL in the environment.
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print(f"[MOCK WEB PUSH] Module 'pywebpush' not installed. Notification to user {user_id}: {title} - {body}")
        return True

    vapid_private_key = os.getenv("VAPID_PRIVATE_KEY")
    from utils.session_helpers import get_company_info
    company = get_company_info()
    admin_email = company.email if (company and company.email) else "admin@aanyaas.com"
    vapid_claims = {"sub": os.getenv("VAPID_CLAIM_EMAIL", f"mailto:{admin_email}")}
    
    if not vapid_private_key:
        print(f"[MOCK WEB PUSH] VAPID keys missing. Notification to user {user_id}: {title} - {body}")
        return True

    subscriptions = get_push_subscriptions(user_id)
    if not subscriptions:
        print(f"No push subscriptions found for user {user_id}")
        return False
        
    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "icon": "/static/img/logo.png"
    })
    
    success_count = 0
    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims  # type: ignore
            )
            success_count += 1
        except WebPushException as ex:
            print(f"Web push failed: {repr(ex)}")
            # Handle invalid subscriptions (e.g. 410 Gone) by removing them from DB
            
    print(f"Sent web push successfully to {success_count} devices for user {user_id}")
    return success_count > 0
