import threading
from flask import render_template, request, redirect, url_for, flash, current_app
from flask_mail import Message
from admin.admin_app import admin_bp, admin_login_required

def send_async_emails(app, messages):
    with app.app_context():
        # Get the mail extension from the app
        mail = current_app.extensions.get('mail')
        if not mail:
            print("Mail extension not found. Cannot send emails.")
            return
            
        success_count = 0
        for msg in messages:
            try:
                mail.send(msg)
                success_count += 1
            except Exception as e:
                print(f"Failed to send email to {msg.recipients}: {e}")
        print(f"Background email broadcast complete: {success_count} sent.")

@admin_bp.route('/marketing/broadcast', methods=['GET', 'POST'])
@admin_login_required
def marketing_broadcast():
    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        body_html = request.form.get('body_html', '').strip()
        
        if not subject or not body_html:
            flash('Both Subject and Content are required.', 'danger')
            return redirect(url_for('admin_bp.marketing_broadcast'))
            
        try:
            from extensions import db
            from models import Subscribers
            subscribers = db.session.scalars(db.select(Subscribers)).all()
            
            if not subscribers:
                flash('No subscribers found.', 'warning')
                return redirect(url_for('admin_bp.marketing_broadcast'))
                
            messages = []
            sender = current_app.config.get('MAIL_DEFAULT_SENDER', 'noreply@aanyaas.com')
            
            # Wrap user content in a clean template
            for sub in subscribers:
                email = sub.email
                msg = Message(subject=subject, sender=sender, recipients=[email])
                
                # Simple branded wrapper
                html_wrapper = f"""
                <div style="font-family: 'Inter', sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden;">
                    <div style="background-color: #a64d79; padding: 20px; text-align: center;">
                        <h1 style="color: white; margin: 0; font-family: 'Playfair Display', serif;">Aanyaas Enterprises</h1>
                    </div>
                    <div style="padding: 30px; background-color: #fff; color: #333; line-height: 1.6;">
                        {body_html}
                    </div>
                    <div style="background-color: #f8f9fa; padding: 15px; text-align: center; color: #718096; font-size: 12px;">
                        You are receiving this email because you subscribed to our newsletter.<br>
                        &copy; 2026 Aanyaas Enterprises. All rights reserved.
                    </div>
                </div>
                """
                msg.html = html_wrapper
                messages.append(msg)
                
            # Start background thread
            app = current_app._get_current_object() # type: ignore
            thread = threading.Thread(target=send_async_emails, args=(app, messages))
            thread.daemon = True
            thread.start()
            
            flash(f'Broadcast queued! Sending {len(messages)} emails in the background.', 'success')
            
        except Exception as e:
            print(f"Broadcast Error: {e}")
            flash('Error launching broadcast.', 'danger')
            
        return redirect(url_for('admin_bp.marketing_broadcast'))
        
    return render_template('admin/marketing_broadcast.html')
