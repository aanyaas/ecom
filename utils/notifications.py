import logging
import os
import json
import requests
import traceback
import threading
from flask import current_app, render_template
from invoice_generator import generate_invoice_pdf
from utils.validation import validate_phone

def get_order_and_customer_data(order_id):
    """Helper to fetch order and customer details."""
    try:
        from extensions import db
        from sqlalchemy import text # type: ignore
        order_row = db.session.execute(text("""
            SELECT o.*, u.email as user_email, u.mobile_number as user_mobile, u.username
            FROM orders o
            JOIN users u ON o.user_id = u.id
            WHERE o.id = :id
        """), {'id': order_id}).fetchone()
        
        if not order_row:
            return None, None
            
        order = dict(order_row._mapping)
            
        try:
            billing_address = json.loads(order['billing_address'])
        except:
            billing_address = {}
            
        try:
            shipping_address = json.loads(order['shipping_address'])
        except:
            shipping_address = {}

        # Prioritize billing address details, fallback to user profile
        email = billing_address.get('email') or order.get('user_email')
        mobile = billing_address.get('phone') or shipping_address.get('phone') or order.get('user_mobile')
        name = billing_address.get('first_name') or order.get('username') or 'Customer'

        customer_info = {
            'email': email,
            'mobile': mobile,
            'name': name
        }
        return order, customer_info
    except Exception as e:
        logging.info(f"Error fetching order data for notifications: {str(e)}")
        return None, None

def send_order_event_email(order_id, event, tracking_info=None):
    """
    Send an email for a specific order event (placed, accepted, shipped, delivered).
    Attaches the invoice PDF.
    """
    if event.lower() == 'rtd':
        logging.info(f"Skipping email notification for RTD event: Order #{order_id}")
        return True
    order, customer = get_order_and_customer_data(order_id)
    if not order or not customer:
        return False
        
    email = customer.get('email', '')
    if not email or not isinstance(email, str) or '@' not in email:
        logging.info(f"Skipping email notification: Invalid email address '{email}'")
        return False
        
    customer['email'] = email.strip()
    try:
        from blueprints.checkout import calculate_gst_breakdown
        gst_data = calculate_gst_breakdown(order_id)
        
        from extensions import db
        from models import OrderItems
        order_items = db.session.scalars(db.select(OrderItems).filter_by(order_id=order_id)).all()
        
        # Render the specific email template for status updates
        import os
        base_url = "https://aanyaas.pythonanywhere.com"
        with current_app.test_request_context(base_url=base_url):
            html = render_template('emails/order_status.html',
                                  order=order,
                                  customer=customer,
                                  event=event,
                                  tracking_info=tracking_info,
                                  gst_breakdown=gst_data,
                                  order_items=order_items)

        mail = current_app.extensions.get('mail')
        if not mail:
            logging.info("Flask-Mail extension not found.")
            return False

        from flask_mail import Message
        
        from utils.session_helpers import get_company_info
        company = get_company_info()
        company_name = company.company_name if company else "Aanyaas Enterprises"
        
        subject_map = {
            'placed': f"Your Order Confirmed #{order['id']} | {company_name}",
            'accepted': f"Your Order #{order['id']} has been Accepted | {company_name}",
            'shipped': f"Your Order #{order['id']} has Shipped! | {company_name}",
            'delivered': f"Your Order #{order['id']} has been Delivered | {company_name}",
            'cancelled': f"Your Order #{order['id']} has been Cancelled | {company_name}",
            'return_requested': f"Return Request Received for Order #{order['id']} | {company_name}",
            'return_approved': f"Return Request Approved for Order #{order['id']} | {company_name}",
            'return_processing': f"Return is being Processed for Order #{order['id']} | {company_name}",
            'return_completed': f"Refund Processed successfully for Order #{order['id']} | {company_name}",
            'return_rejected': f"Return Request Rejected for Order #{order['id']} | {company_name}"
        }
        subject = subject_map.get(event.lower(), f"Update on Order #{order['id']} | {company_name}")

        msg = Message(subject, recipients=[customer['email']], html=html)

        # Generate and attach the invoice PDF only for Shipped
        if event.lower() == 'shipped':
            try:
                # generate_invoice_pdf expects raw conn, so we must use SQLAlchemy engine raw connection
                from extensions import db
                engine = db.engine
                with engine.connect() as connection:
                    raw_conn = connection.connection
                    pdf_buffer, error = generate_invoice_pdf(order_id, raw_conn, current_app)
                    if not error and pdf_buffer:
                        msg.attach(f"invoice_{order_id}.pdf", "application/pdf", pdf_buffer.getvalue())
                    else:
                        logging.info(f"Could not attach PDF for order {order_id}: {error}")
            except Exception as pdf_e:
                logging.info(f"Exception while generating PDF for email attachment: {str(pdf_e)}")

        mail.send(msg)
        logging.info(f"Successfully sent {event} email to {customer['email']}")
        return True

    except Exception as e:
        logging.info(f"Error sending order event email ({event}): {str(e)}")
        traceback.print_exc()
        return False

def send_order_event_whatsapp(order_id, event, tracking_info=None):
    """
    Integrates with Meta's WhatsApp Cloud API to send order status updates.
    Provides provisions for dynamic templates.
    """
    order, customer = get_order_and_customer_data(order_id)
    if not order or not customer or not customer.get('mobile'):
        return False

    mobile = customer['mobile']
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    phone_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    version = os.getenv('WHATSAPP_API_VERSION', 'v17.0')

    if not access_token or not phone_id or 'your_meta' in access_token:
        logging.info("WhatsApp credentials missing or placeholder in .env. Skipping WA notification.")
        return False

    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    clean_mobile = str(mobile).strip()
    if len(clean_mobile) == 10:
        clean_mobile = f"91{clean_mobile}"

    tracking_info = tracking_info or {}
    from config_manager import get_config
    brand_name = get_config('BRAND_NAME', 'Aanyaas')
    
    # Determine message content based on event
    messages = {
        'placed': f"Hello {customer['name']}, your order #{order['id']} at {brand_name} has been placed successfully. Thank you for shopping with us!",
        'accepted': f"Hello {customer['name']}, good news! Your order #{order['id']} has been accepted and is currently being processed.",
        'shipped': f"Hello {customer['name']}, your order #{order['id']} has been shipped! Courier: {tracking_info.get('courier_name', 'Standard')} Tracking: {tracking_info.get('tracking_id', 'N/A')}",
        'delivered': f"Hello {customer['name']}, your order #{order['id']} has been delivered. We hope you enjoy your purchase!",
        'cancelled': f"Hello {customer['name']}, your order #{order['id']} has been cancelled. Reason: {tracking_info.get('reason', 'N/A')}.",
        'return_requested': f"Hello {customer['name']}, your return request for order #{order['id']} has been received. Reason: {tracking_info.get('reason', 'N/A')}.",
        'return_approved': f"Hello {customer['name']}, good news! Your return request for order #{order['id']} has been approved.",
        'return_processing': f"Hello {customer['name']}, we are currently processing the return for order #{order['id']}.",
        'return_completed': f"Hello {customer['name']}, your return for order #{order['id']} is complete. A refund of ₹{tracking_info.get('refund_amount', '0')} has been processed.",
        'return_rejected': f"Hello {customer['name']}, we regret to inform you that your return request for order #{order['id']} was rejected. Reason: {tracking_info.get('reason', 'N/A')}."
    }
    
    text_body = messages.get(event.lower(), f"Update on your order #{order['id']}.")

    # IMPORTANT: If sending outside 24h window, this MUST use a pre-approved template format.
    # Below is a standard text message. If you create templates in Meta, switch the 'type' to 'template'.
    data = {
        "messaging_product": "whatsapp",
        "to": clean_mobile,
        "type": "text",
        "text": {"body": text_body}
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code == 200:
            logging.info(f"WhatsApp {event} notification successfully sent to {mobile}")
            return True
        else:
            logging.info(f"WhatsApp API Error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logging.info(f"WhatsApp Request Failed: {str(e)}")
        return False

def send_order_event_sms(order_id, event, tracking_info=None):
    """
    Production-ready SMS framework for MSG91 and Twilio.
    Triggers when SMS_PROVIDER is set to 'twilio' or 'msg91' in .env.
    """
    order, customer = get_order_and_customer_data(order_id)
    if not order or not customer or not customer.get('mobile'):
        return False

    mobile = customer['mobile']
    sms_api_key = os.getenv('SMS_API_KEY')
    sms_provider = os.getenv('SMS_PROVIDER', 'mock').lower()
    
    tracking_info = tracking_info or {}
    from config_manager import get_config
    brand_name = get_config('BRAND_NAME', 'Aanyaas')
    
    messages = {
        'placed': f"{brand_name}: Order #{order['id']} placed successfully.",
        'accepted': f"{brand_name}: Order #{order['id']} is accepted and processing.",
        'shipped': f"{brand_name}: Order #{order['id']} shipped. Track: {tracking_info.get('tracking_id', '')}",
        'delivered': f"{brand_name}: Order #{order['id']} delivered.",
        'cancelled': f"{brand_name}: Order #{order['id']} cancelled. Reason: {tracking_info.get('reason', '')}",
        'return_requested': f"{brand_name}: Return requested for Order #{order['id']}.",
        'return_approved': f"{brand_name}: Return approved for Order #{order['id']}.",
        'return_processing': f"{brand_name}: Return processing for Order #{order['id']}.",
        'return_completed': f"{brand_name}: Return complete for Order #{order['id']}. Refund: Rs.{tracking_info.get('refund_amount', '0')}.",
        'return_rejected': f"{brand_name}: Return rejected for Order #{order['id']}."
    }
    
    text_body = messages.get(event.lower(), f"{brand_name}: Order #{order['id']} updated.")
    
    if sms_provider == 'mock' or not sms_api_key:
        logging.info(f"[MOCK SMS] To: {mobile} | Body: {text_body}")
        return True
        
    try:
        if sms_provider == 'msg91':
            # MSG91 Integration
            url = "https://api.msg91.com/api/v5/flow/"
            headers = {"authkey": sms_api_key, "Content-Type": "application/json"}
            payload = {
                "template_id": os.getenv('MSG91_TEMPLATE_ID_ORDER'),
                "short_url": "1",
                "recipients": [{"mobiles": str(mobile).strip(), "var1": str(order['id']), "var2": text_body}]
            }
            res = requests.post(url, json=payload, headers=headers, timeout=10)
            res.raise_for_status()
            logging.info(f"[MSG91 SMS] Sent to {mobile}")
            
        elif sms_provider == 'twilio':
            # Twilio Integration (Fallback using requests to avoid requiring twilio pip package if not installed)
            account_sid = os.getenv('TWILIO_ACCOUNT_SID')
            from_phone = os.getenv('TWILIO_PHONE')
            
            if not account_sid or not from_phone:
                logging.info("Missing Twilio configuration (TWILIO_ACCOUNT_SID or TWILIO_PHONE)")
                return False
                
            url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
            payload = {
                "To": f"+91{str(mobile).strip()[-10:]}",
                "From": from_phone,
                "Body": text_body
            }
            res = requests.post(url, data=payload, auth=(account_sid, sms_api_key), timeout=10)
            res.raise_for_status()
            logging.info(f"[TWILIO SMS] Sent to {mobile}")
            
    except Exception as e:
        logging.info(f"SMS API Error ({sms_provider}): {str(e)}")
        return False
        
    return True

import uuid

def run_notifications_job(order_id, event, tracking_info):
    """Picklable background job function for APScheduler."""
    from app import app
    with app.app_context():
        logging.info(f"Triggering notifications for Order #{order_id} - Event: {event}")
        email_sent = send_order_event_email(order_id, event, tracking_info)
        wa_sent = send_order_event_whatsapp(order_id, event, tracking_info)
        sms_sent = send_order_event_sms(order_id, event, tracking_info)
        
        # Fire Web Push
        from utils.web_push import send_web_push
        order, customer = get_order_and_customer_data(order_id)
        if order and order.get('user_id'):
            title = f"Order {event.capitalize()}"
            body = f"Your order #{order_id} has been {event}."
            send_web_push(order['user_id'], title, body, url=f"/orders/{order_id}")
            
        logging.info(f"Notifications completed for Order #{order_id}")

def trigger_all_order_notifications(order_id, event, tracking_info=None):
    """Convenience function to fire all notifications in the background."""
    logging.info(f"Queuing notifications for Order #{order_id} - Event: {event}")
    from app import scheduler
    
    scheduler.add_job(
        func=run_notifications_job,
        trigger='date', # Run immediately
        args=(order_id, event, tracking_info),
        id=f"notify_{order_id}_{event}_{uuid.uuid4().hex[:8]}"
    )
    
    return {
        "email": "queued",
        "whatsapp": "queued",
        "sms": "queued",
        "push": "queued"
    }
