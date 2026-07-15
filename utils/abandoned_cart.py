import os
import json
import traceback
from datetime import datetime, timedelta, UTC
from flask import current_app, render_template
from flask_mail import Message

def process_abandoned_carts():
    """
    Checks for shopping carts that have been idle for over 24 hours.
    Sends a reminder email if the user hasn't made a recent purchase.
    To be run as a background cron job via APScheduler.
    """
    # Need app context because APScheduler runs in a background thread
    # The actual app instance needs to be passed, but since it's a global we can grab it from current_app if available
    # Actually, we should fetch it directly or pass it, but since we are scheduling this in app.py, 
    # we can use current_app if we push an app context. Wait, current_app is local to the request.
    # To fix this, we will import 'app' from app.py at runtime to avoid circular imports.
    try:
        from app import app
    except ImportError:
        print("Could not import app instance for abandoned cart job.")
        return

    with app.app_context():
        print(f"[ABANDONED CART] Starting scan at {datetime.now(UTC)}")
        try:
            from extensions import db
            from sqlalchemy import text  # type: ignore
            
            # Find users with items in their cart updated more than 24 hours ago, but less than 48 hours ago
            cutoff_start = datetime.now() - timedelta(hours=48)
            cutoff_end = datetime.now() - timedelta(hours=24)
            
            # Get users with abandoned carts
            query = """
                SELECT DISTINCT c.user_id, u.email, u.username, u.first_name 
                FROM cart c
                JOIN users u ON c.user_id = u.id
                WHERE (c.updated_at IS NOT NULL AND c.updated_at BETWEEN :start AND :end)
                   OR (c.updated_at IS NULL AND c.created_at BETWEEN :start AND :end)
            """
            abandoned_users = db.session.execute(text(query), {'start': cutoff_start, 'end': cutoff_end}).fetchall()
            
            sent_count = 0
            for user in abandoned_users:
                user_id = user.user_id
                
                # Check if user made an order in the last 24 hours
                order_check_query = """
                    SELECT id FROM orders 
                    WHERE user_id = :uid AND created_at > :cutoff
                    LIMIT 1
                """
                recent_order = db.session.execute(text(order_check_query), {'uid': user_id, 'cutoff': cutoff_end}).fetchone()
                
                if recent_order:
                    continue
                    
                # Fetch their cart items to show in the email
                items_query = """
                    SELECT c.quantity, p.name, p.image, p.price, p.mrp
                    FROM cart c
                    JOIN products p ON c.product_id = p.id
                    WHERE c.user_id = :uid
                """
                cart_items = db.session.execute(text(items_query), {'uid': user_id}).fetchall()
                
                if not cart_items:
                    continue
                    
                # Format Name
                customer_name = user.first_name or user.username or 'Customer'
                
                # Calculate totals
                cart_total = sum(float(item.price) * item.quantity for item in cart_items)
                
                # Render Email
                html = render_template(
                    'emails/abandoned_cart.html',
                    customer_name=customer_name,
                    cart_items=[dict(row._mapping) for row in cart_items],
                    cart_total=cart_total
                )
                
                mail = app.extensions.get('mail')
                if mail and user.email:
                    msg = Message(
                        subject="Did you forget something? Your cart is waiting! 🛍️",
                        recipients=[user.email],
                        html=html
                    )
                    try:
                        mail.send(msg)
                        sent_count += 1
                        print(f"[ABANDONED CART] Sent recovery email to {user.email}")
                        
                        # Optionally: touch the cart updated_at to prevent re-sending for another 24 hours
                        update_cart = "UPDATE cart SET updated_at = NOW() WHERE user_id = :uid"
                        db.session.execute(text(update_cart), {'uid': user_id})
                        db.session.commit()
                    except Exception as email_err:
                        db.session.rollback()
                        print(f"[ABANDONED CART] Failed to send email to {user.email}: {email_err}")
                        
            print(f"[ABANDONED CART] Scan complete. Sent {sent_count} recovery emails.")
            
        except Exception as e:
            print(f"[ABANDONED CART] Error processing carts: {e}")
            traceback.print_exc()
        finally:
            try:
                from extensions import db
                db.session.remove()
            except Exception:
                pass
