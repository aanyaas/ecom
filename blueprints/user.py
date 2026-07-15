import os
import time
import secrets
import bcrypt
import traceback
import requests
from datetime import datetime, timedelta, UTC
from functools import wraps
from werkzeug.utils import secure_filename

from flask import Blueprint, current_app, render_template, request, session, redirect, url_for, jsonify, flash
from flask_mail import Message

from extensions import db
from models import Users, UserAddresses, LoyaltyLedger, Cart, GuestCart, Products, Coupons, OrderReturns, ReturnItems, PasswordResetTokens, OtpVerifications
from utils.validation import validate_email, validate_phone
from utils.session_helpers import get_guest_or_user_cart_count

user_bp = Blueprint('user_bp', __name__)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth_bp.login', next=request.url))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@user_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        hp_field = request.form.get('hp_field', '')

        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        # Security: Honeypot check
        if hp_field:
            print("--- SECURITY LOG: Honeypot triggered (Bot attempt) ---")
            return jsonify({'success': False, 'message': 'Bot detected.'}), 400

        if not identifier or not password:
            msg = 'Email/Mobile and password are required'
            if is_ajax:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'error')
            return redirect(url_for('pages_bp.home'))

        email = None
        mobile = None
        username = identifier

        if '@' in identifier:
            email = identifier
            if not validate_email(email):
                msg = 'Please enter a valid email address.'
                if is_ajax:
                    return jsonify({'success': False, 'message': msg}), 400
                flash(msg, 'error')
                return redirect(url_for('pages_bp.home'))
        elif identifier.isdigit() and len(identifier) >= 10:
            mobile = identifier
        else:
            msg = 'Please enter a valid email or 10-digit mobile number.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'error')
            return redirect(url_for('pages_bp.home'))

        if len(password) < 8:
            msg = 'Password must be at least 8 characters long.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'error')
            return redirect(url_for('pages_bp.home'))

        if password != confirm_password:
            msg = 'Password and confirm password do not match.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'error')
            return redirect(url_for('pages_bp.home'))

        try:
            conditions = [Users.username == username]
            if email:
                conditions.append(Users.email == email)
            if mobile:
                conditions.append(Users.mobile_number == mobile)

            existing_user = db.session.scalars(
                db.select(Users).filter(db.or_(*conditions))
            ).first()

            if existing_user:
                msg = 'This Email or Mobile Number is already registered.'
                if is_ajax:
                    return jsonify({'success': False, 'message': msg}), 400
                flash(msg, 'error')
                return redirect(url_for('pages_bp.home'))

            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            # Check for referral in session
            ref_code = session.get('referral_code')
            referred_by_id = None
            if ref_code:
                referrer = db.session.scalars(db.select(Users).filter_by(referral_code=ref_code)).first()
                if referrer:
                    referred_by_id = referrer.id

            import string
            base = ''.join(c for c in username if c.isalnum()).upper()[:4]
            random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
            new_referral_code = f"{base}{random_part}"

            new_user = Users(
                username=username,
                password=hashed_password,
                email=email,
                mobile_number=mobile,
                first_name='',
                last_name='',
                referral_code=new_referral_code,
                referred_by=referred_by_id
            )
            db.session.add(new_user)
            db.session.flush() # Get the new user ID without committing yet
            
            new_user_id = new_user.id
            new_referral_code = f"{base}{new_user_id}{random_part}"
            new_user.referral_code = new_referral_code

            if referred_by_id:
                db.session.add(LoyaltyLedger(user_id=referred_by_id, points=100, transaction_type='referral_bonus'))
                db.session.add(LoyaltyLedger(user_id=new_user_id, points=100, transaction_type='signup_bonus'))

            db.session.commit()

            if is_ajax:
                return jsonify({'success': True, 'message': 'Registration successful! Please login.'})
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('auth_bp.login'))

        except Exception as e:
            db.session.rollback()
            print("Unexpected error during registration:", e)
            traceback.print_exc()
            msg = f'Registration failed due to an unexpected error: {str(e)}. Please try again.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg}), 500
            flash(msg, 'error')
            return redirect(url_for('pages_bp.home'))

    return redirect(url_for('pages_bp.home'))


# ---------------------------------------------------------------------------
# WhatsApp OTP
# ---------------------------------------------------------------------------

def _send_whatsapp_message(mobile, otp):
    """
    Integrates with Meta's WhatsApp Cloud API to send automated OTPs.
    """
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    phone_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    version = os.getenv('WHATSAPP_API_VERSION', 'v17.0')

    if not access_token or not phone_id or 'your_meta' in access_token:
        print("--- SECURITY LOG: WhatsApp credentials missing or placeholder in .env ---")
        return False

    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    clean_mobile = str(mobile).strip()
    if len(clean_mobile) == 10:
        clean_mobile = f"91{clean_mobile}"

    data = {
        "messaging_product": "whatsapp",
        "to": clean_mobile,
        "type": "text",
        "text": {"body": f"Your Aanyaas login OTP is: {otp}. It is valid for 5 minutes. Do not share this with anyone."}
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code == 200:
            print(f"--- SECURITY LOG: WhatsApp OTP successfully sent to {mobile} ---")
            return True
        else:
            print(f"--- SECURITY LOG: WhatsApp API Error ({response.status_code}): {response.text} ---")
            return False
    except Exception as e:
        print(f"--- SECURITY LOG: WhatsApp Request Failed: {str(e)} ---")
        return False


@user_bp.route('/send-whatsapp-otp', methods=['POST'])
def send_whatsapp_otp():
    last_sent = session.get('last_wa_otp_time')
    if last_sent:
        elapsed = (datetime.now(UTC).replace(tzinfo=None) - datetime.fromisoformat(last_sent)).total_seconds()
        if elapsed < 60:
            return jsonify({'success': False, 'message': f'Please wait {int(60-elapsed)}s before resending.'}), 429

    conn = None
    try:
        mobile = request.form.get('mobile', '').strip()
        if not mobile:
            return jsonify({'success': False, 'message': 'Mobile number is required'}), 400

        user = db.session.scalars(db.select(Users).filter_by(mobile_number=mobile)).first()
        if not user:
            return jsonify({'success': False, 'message': 'No account found with this mobile number.'}), 404

        otp = str(secrets.randbelow(900000) + 100000)
        expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)

        db.session.add(OtpVerifications(email=f"WA_{mobile}", otp=otp, expires_at=expires_at))
        db.session.commit()

        if _send_whatsapp_message(mobile, otp):
            session['last_wa_otp_time'] = datetime.now(UTC).replace(tzinfo=None).isoformat()
            return jsonify({'success': True, 'message': 'OTP sent successfully to your WhatsApp.'})
        else:
            return jsonify({'success': False, 'message': 'Failed to send WhatsApp message.'}), 500

    except Exception as e:
        db.session.rollback()
        print(f"Error sending WhatsApp OTP: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred. Please try again.'}), 500


@user_bp.route('/verify-whatsapp-otp', methods=['POST'])
def verify_whatsapp_otp():
    try:
        mobile = request.form.get('mobile', '').strip()
        otp = request.form.get('otp', '').strip()

        if not mobile or not otp:
            return jsonify({'success': False, 'message': 'Mobile and OTP are required'}), 400

        otp_record = db.session.scalars(
            db.select(OtpVerifications)
            .filter(OtpVerifications.email == f"WA_{mobile}", OtpVerifications.otp == otp, OtpVerifications.expires_at > db.func.utc_timestamp())
            .order_by(OtpVerifications.created_at.desc())
        ).first()

        if not otp_record:
            return jsonify({'success': False, 'message': 'Invalid or expired OTP.'}), 400

        user = db.session.scalars(db.select(Users).filter_by(mobile_number=mobile)).first()

        if not user:
            return jsonify({'success': False, 'message': 'User not found.'}), 404

        guest_id = session.get('guest_id') or request.cookies.get('guest_id')

        session.clear()
        session['user_id'] = user.id
        session['username'] = user.username
        session['email'] = user.email

        if guest_id:
            try:
                guest_carts = db.session.scalars(db.select(GuestCart).filter_by(guest_id=guest_id)).all()
                for gc in guest_carts:
                    cart_item = db.session.scalars(db.select(Cart).filter_by(user_id=user.id, product_id=gc.product_id)).first()
                    if cart_item:
                        cart_item.quantity += gc.quantity
                    else:
                        db.session.add(Cart(user_id=user.id, product_id=gc.product_id, quantity=gc.quantity))
                    db.session.delete(gc)
            except Exception as e:
                print(f"Error merging carts: {str(e)}")

        db.session.execute(db.delete(OtpVerifications).where(OtpVerifications.email == f"WA_{mobile}"))
        db.session.commit()

        return jsonify({'success': True, 'message': 'Login successful!', 'next': request.form.get('next')})
    except Exception as e:
        db.session.rollback()
        print(f"Error verifying WhatsApp OTP: {str(e)}")
        return jsonify({'success': False, 'message': 'Verification failed. Please try again.'}), 500


# ---------------------------------------------------------------------------
# Forgot / Reset Password
# ---------------------------------------------------------------------------

@user_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    try:
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if request.method == 'POST':
            email = request.form.get('email', '').strip()
            if not email:
                if is_ajax:
                    return jsonify({'success': False, 'message': 'Please enter your email address'}), 400
                flash('Please enter your email address', 'error')
                return redirect(url_for('user_bp.forgot_password'))
            
            user = db.session.scalars(db.select(Users).filter_by(email=email)).first()
            if not user:
                if is_ajax:
                    return jsonify({'success': True, 'message': 'If an account exists with this email, a password reset link has been sent'})
                flash('If an account exists with this email, a password reset link has been sent', 'info')
                return redirect(url_for('auth_bp.login'))
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10)
            
            db.session.add(PasswordResetTokens(user_id=user.id, token=token, expires_at=expires_at))
            db.session.commit()

            reset_link = url_for('user_bp.reset_password', token=token, _external=True)
            mail = current_app.extensions.get('mail')
            if mail:
                msg = Message(
                    "Password Reset Request",
                    sender=current_app.config.get('MAIL_DEFAULT_SENDER'),
                    recipients=[email],
                    body=f"Hello {user.username},\n\nClick the following link to reset your password:\n{reset_link}\n\nThis link expires in 10 minutes."
                )
                mail.send(msg)
            if is_ajax:
                return jsonify({'success': True, 'message': 'Password reset link has been sent to your email'})
            flash('Password reset link has been sent to your email', 'success')
            return redirect(url_for('auth_bp.login'))
        return render_template('forgot_password.html', user_logged_in='user_id' in session)
    except Exception as e:
        db.session.rollback()
        print(f"Error in forgot password: {str(e)}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Error processing your request. Please try again.'}), 500
        flash('Error processing your request. Please try again.', 'error')
        return redirect(url_for('user_bp.forgot_password'))


@user_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        token_record = db.session.scalars(
            db.select(PasswordResetTokens)
            .filter(PasswordResetTokens.token == token, PasswordResetTokens.used == 0, PasswordResetTokens.expires_at > db.func.utc_timestamp())
        ).first()
        
        if not token_record:
            flash('Invalid or expired password reset link', 'error')
            return redirect(url_for('user_bp.forgot_password'))
            
        user = token_record.user
        
        if request.method == 'POST':
            password = request.form.get('password', '').strip()
            confirm_password = request.form.get('confirm_password', '').strip()
            if not password or not confirm_password:
                flash('Both fields are required', 'error')
                return redirect(url_for('user_bp.reset_password', token=token))
            if password != confirm_password:
                flash('Passwords do not match', 'error')
                return redirect(url_for('user_bp.reset_password', token=token))
            if len(password) < 8:
                flash('Password must be at least 8 characters', 'error')
                return redirect(url_for('user_bp.reset_password', token=token))
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            user.password = hashed_password
            user.updated_at = db.func.current_date()
            token_record.used = 1
            
            db.session.commit()
            flash('Password reset successfully! Please login with your new password', 'success')
            return redirect(url_for('auth_bp.login'))
        return render_template('reset_password.html', token=token, email=user.email, user_logged_in='user_id' in session)
    except Exception as e:
        db.session.rollback()
        print(f"Error resetting password: {str(e)}")
        flash('Error resetting password', 'error')
        return redirect(url_for('user_bp.reset_password', token=token))


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@user_bp.route('/profile')
@login_required
def profile():
    try:
        user = db.session.scalars(db.select(Users).filter_by(id=session['user_id'])).first()
        if not user:
            flash('User not found', 'error')
            return redirect(url_for('pages_bp.home'))
            
        addresses = db.session.scalars(db.select(UserAddresses).filter_by(user_id=session['user_id']).order_by(UserAddresses.is_default.desc(), UserAddresses.address_type)).all()
        
        user_loyalty_points = db.session.query(db.func.coalesce(db.func.sum(LoyaltyLedger.points), 0)).filter_by(user_id=session['user_id']).scalar()
        loyalty_transactions = db.session.scalars(db.select(LoyaltyLedger).filter_by(user_id=session['user_id']).order_by(LoyaltyLedger.created_at.desc())).all()

        # Convert to dict for template compatibility if needed, but objects should work directly if template uses dot notation
        # Or safely construct a dict:
        user_dict = {
            'id': user.id, 'username': user.username, 'email': user.email, 'mobile_number': user.mobile_number,
            'first_name': user.first_name, 'last_name': user.last_name, 'alternate_mobile': user.alternate_mobile,
            'gender': user.gender, 'gstin': user.gstin, 'date_of_birth': user.date_of_birth,
            'marriage_anniversary': user.marriage_anniversary, 'profile_picture': user.profile_picture,
            'referral_code': user.referral_code, 'created_at': user.created_at
        }
        addresses_dicts = []
        for addr in addresses:
            addresses_dicts.append({
                'id': addr.id, 'address_type': addr.address_type, 'full_name': addr.full_name,
                'mobile_number': addr.mobile_number, 'email': addr.email, 'company_name': addr.company_name,
                'gst_number': addr.gst_number, 'address_line1': addr.address_line1, 'address_line2': addr.address_line2,
                'city': addr.city, 'state': addr.state, 'state_code': addr.state_code, 'postal_code': addr.postal_code,
                'country': addr.country, 'is_default': addr.is_default
            })

        return render_template('profile.html', user=user_dict, addresses=addresses_dicts, user_logged_in=True, username=session.get("username"), user_loyalty_points=user_loyalty_points, loyalty_transactions=loyalty_transactions)
    except Exception as e:
        print(f"Error loading profile: {str(e)}")
        flash('Error loading profile. Please try again.', 'error')
        return redirect(url_for('pages_bp.home'))


@user_bp.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    try:
        user = db.session.scalars(db.select(Users).filter_by(id=session['user_id'])).first()
        if not user:
            flash('User not found', 'error')
            return redirect(url_for('pages_bp.home'))
            
        if request.method == 'POST':
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            email = request.form.get('email', '').strip()
            mobile_number = request.form.get('mobile_number', '').strip()
            alternate_mobile = request.form.get('alternate_mobile', '').strip()
            gender = request.form.get('gender', '').strip()
            gstin = request.form.get('gstin', '').strip()
            date_of_birth = request.form.get('date_of_birth', None)
            marriage_anniversary = request.form.get('marriage_anniversary', None)

            date_of_birth = date_of_birth if date_of_birth else None
            marriage_anniversary = marriage_anniversary if marriage_anniversary else None

            if email and not validate_email(email):
                flash('Please enter a valid email address', 'error')
                return redirect(url_for('user_bp.edit_profile'))
            if mobile_number and not validate_phone(mobile_number):
                flash('Please enter a valid mobile number', 'error')
                return redirect(url_for('user_bp.edit_profile'))

            # Check for existing email on other accounts
            if email and email != user.email:
                existing = db.session.scalars(db.select(Users).filter(Users.email == email, Users.id != user.id)).first()
                if existing:
                    flash('Email is already in use by another account', 'error')
                    return redirect(url_for('user_bp.edit_profile'))

            # Profile Picture Upload
            profile_picture = None
            if 'profile_picture' in request.files:
                file = request.files['profile_picture']
                if file and file.filename != '':
                    filename = secure_filename(f"user_{session['user_id']}_{int(time.time())}_{file.filename}")
                    file_path = os.path.join(current_app.config['PROFILE_IMAGE_DIR'], filename)
                    file.save(file_path)
                    profile_picture = filename

            user.first_name = first_name or None
            user.last_name = last_name or None
            user.email = email or None
            user.mobile_number = mobile_number or None
            user.alternate_mobile = alternate_mobile or None
            user.gender = gender or None
            user.gstin = gstin or None
            user.date_of_birth = date_of_birth
            user.marriage_anniversary = marriage_anniversary
            user.updated_at = db.func.current_date()
            
            if profile_picture:
                user.profile_picture = profile_picture

            db.session.commit()
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('user_bp.profile'))
        else:
            return render_template('edit_profile.html', user=user, user_logged_in=True,
                                   username=session.get("username"), cart_count=get_guest_or_user_cart_count())
    except Exception as e:
        db.session.rollback()
        print(f"Error updating profile: {str(e)}")
        flash('Error updating profile', 'error')
        return redirect(url_for('user_bp.edit_profile'))


@user_bp.route('/profile/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    try:
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if request.method == 'POST':
            current_password = request.form.get('current_password', '').strip()
            new_password = request.form.get('new_password', '').strip()
            confirm_password = request.form.get('confirm_password', '').strip()
            
            if not current_password or not new_password or not confirm_password:
                msg = 'All fields are required'
                if is_ajax: return jsonify({'success': False, 'message': msg}), 400
                flash(msg, 'error')
                return render_template('change_password.html', user_logged_in=True, username=session.get("username"), cart_count=get_guest_or_user_cart_count())
                
            if new_password != confirm_password:
                msg = 'New passwords do not match'
                if is_ajax: return jsonify({'success': False, 'message': msg}), 400
                flash(msg, 'error')
                return render_template('change_password.html', user_logged_in=True, username=session.get("username"), cart_count=get_guest_or_user_cart_count())
                
            if len(new_password) < 8:
                msg = 'Password must be at least 8 characters'
                if is_ajax: return jsonify({'success': False, 'message': msg}), 400
                flash(msg, 'error')
                return render_template('change_password.html', user_logged_in=True, username=session.get("username"), cart_count=get_guest_or_user_cart_count())
                                       
            user = db.session.scalars(db.select(Users).filter_by(id=session['user_id'])).first()
            if not user or not bcrypt.checkpw(current_password.encode('utf-8'), user.password.encode('utf-8')):
                msg = 'Current password is incorrect'
                if is_ajax: return jsonify({'success': False, 'message': msg}), 400
                flash(msg, 'error')
                return render_template('change_password.html', user_logged_in=True, username=session.get("username"))
                
            hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            user.password = hashed_password
            user.updated_at = db.func.current_date()
            
            db.session.commit()
            msg = 'Password changed successfully!'
            if is_ajax: return jsonify({'success': True, 'message': msg})
            flash(msg, 'success')
            return redirect(url_for('user_bp.profile'))
            
        return render_template('change_password.html', user_logged_in=True, username=session.get("username"))
    except Exception as e:
        db.session.rollback()
        print(f"Error changing password: {str(e)}")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({'success': False, 'message': 'Internal Server Error'}), 500
        flash('Error changing password', 'error')
        return redirect(url_for('user_bp.change_password'))


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

@user_bp.route('/profile/address/add', methods=['GET', 'POST'])
@login_required
def add_address():
    try:
        user = db.session.scalars(db.select(Users).filter_by(id=session['user_id'])).first()
        
        if request.method == 'POST':
            address_type = request.form.get('address_type', 'home')
            full_name = request.form.get('full_name', '').strip()
            mobile_number = request.form.get('mobile_number', '').strip()
            email = request.form.get('email', '').strip()
            address_line1 = request.form.get('address_line1', '').strip()
            address_line2 = request.form.get('address_line2', '').strip()
            city = request.form.get('city', '').strip()
            state = request.form.get('state', '').strip()
            postal_code = request.form.get('postal_code', '').strip()
            company_name = request.form.get('company_name', '').strip()
            gst_number = request.form.get('gst_number', '').strip()
            is_default = request.form.get('is_default') in ('1', 'on', 'true', 'yes')
            required_fields = {
                'full_name': full_name, 'mobile_number': mobile_number, 'email': email,
                'address_line1': address_line1, 'city': city, 'state': state, 'postal_code': postal_code
            }
            if address_type == 'bill':
                required_fields['gst_number'] = gst_number

            missing_fields = [field for field, value in required_fields.items() if not value]
            if missing_fields:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'message': f'Missing required fields: {", ".join(missing_fields)}'})
                flash(f'Missing required fields: {", ".join(missing_fields)}', 'error')
                return redirect(url_for('user_bp.add_address'))
            if not validate_phone(mobile_number):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'message': 'Please enter a valid mobile number'})
                flash('Please enter a valid mobile number', 'error')
                return redirect(url_for('user_bp.add_address'))
                
            if is_default:
                db.session.execute(db.update(UserAddresses).where(UserAddresses.user_id == session['user_id']).values(is_default=False))

            new_address = UserAddresses(
                user_id=session['user_id'],
                address_type=address_type,
                full_name=full_name,
                mobile_number=mobile_number,
                address_line1=address_line1,
                address_line2=address_line2 or None,
                city=city,
                state=state,
                postal_code=postal_code,
                is_default=is_default,
                state_code=request.form.get('state_code'),
                email=email,
                company_name=company_name or None,
                gst_number=gst_number or None
            )
            db.session.add(new_address)
            db.session.commit()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': True, 'message': 'Address added successfully!'})
            flash('Address added successfully!', 'success')
            return redirect(url_for('user_bp.profile'))
            
        return render_template('add_address.html', user=user, user_logged_in=True, username=session.get("username"))
    except Exception as e:
        db.session.rollback()
        print(f"Error adding address: {str(e)}")
        flash('Error adding address', 'error')
        return redirect(url_for('user_bp.add_address'))


@user_bp.route('/profile/address/edit/<int:address_id>', methods=['GET', 'POST'])
@login_required
def edit_address(address_id):
    try:
        address = db.session.scalars(db.select(UserAddresses).filter_by(id=address_id, user_id=session['user_id'])).first()
        if not address:
            flash('Address not found', 'error')
            return redirect(url_for('user_bp.profile'))
            
        if request.method == 'POST':
            address_type = request.form.get('address_type', 'home')
            full_name = request.form.get('full_name', '').strip()
            mobile_number = request.form.get('mobile_number', '').strip()
            email = request.form.get('email', '').strip()
            address_line1 = request.form.get('address_line1', '').strip()
            address_line2 = request.form.get('address_line2', '').strip()
            city = request.form.get('city', '').strip()
            state = request.form.get('state', '').strip()
            postal_code = request.form.get('postal_code', '').strip()
            company_name = request.form.get('company_name', '').strip()
            gst_number = request.form.get('gst_number', '').strip()
            is_default = request.form.get('is_default') in ('1', 'on', 'true', 'yes')
            
            required_fields = {
                'full_name': full_name, 'mobile_number': mobile_number, 'address_line1': address_line1,
                'city': city, 'state': state, 'postal_code': postal_code
            }
            if address_type == 'bill':
                required_fields['gst_number'] = gst_number
            
            missing_fields = [field for field, value in required_fields.items() if not value]
            if missing_fields:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'message': f'Missing required fields: {", ".join(missing_fields)}'})
                flash(f'Missing required fields: {", ".join(missing_fields)}', 'error')
                return redirect(url_for('user_bp.edit_address', address_id=address_id))
            if not validate_phone(mobile_number):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'message': 'Please enter a valid mobile number'})
                flash('Please enter a valid mobile number', 'error')
                return redirect(url_for('user_bp.edit_address', address_id=address_id))
                
            if is_default and not address.is_default:
                db.session.execute(db.update(UserAddresses).where(UserAddresses.user_id == session['user_id']).values(is_default=False))
                
            address.address_type = address_type
            address.full_name = full_name
            address.mobile_number = mobile_number
            address.address_line1 = address_line1
            address.address_line2 = address_line2 or None
            address.city = city
            address.state = state
            address.postal_code = postal_code
            address.is_default = is_default
            address.state_code = request.form.get('state_code')
            address.email = email or None
            address.company_name = company_name or None
            address.gst_number = gst_number or None
            address.updated_at = db.func.current_timestamp()
            
            db.session.commit()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': True, 'message': 'Address updated successfully!'})
            flash('Address updated successfully!', 'success')
            next_page = request.args.get('next') or request.form.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('user_bp.profile'))
            
        next_page = request.args.get('next')
        # Convert to dict for template compat
        addr_dict = {
            'id': address.id, 'address_type': address.address_type, 'full_name': address.full_name,
            'mobile_number': address.mobile_number, 'email': address.email, 'company_name': address.company_name,
            'gst_number': address.gst_number, 'address_line1': address.address_line1, 'address_line2': address.address_line2,
            'city': address.city, 'state': address.state, 'state_code': address.state_code, 'postal_code': address.postal_code,
            'country': address.country, 'is_default': address.is_default
        }
        return render_template('edit_address.html', address=addr_dict, user_logged_in=True, username=session.get("username"), next=next_page)
    except Exception as e:
        db.session.rollback()
        print(f"Error editing address: {str(e)}")
        flash('Error editing address', 'error')
        return redirect(url_for('user_bp.edit_address', address_id=address_id))


@user_bp.route('/profile/address/delete/<int:address_id>', methods=['POST'])
@login_required
def delete_address(address_id):
    try:
        address = db.session.scalars(db.select(UserAddresses).filter_by(id=address_id, user_id=session['user_id'])).first()
        if not address:
            return jsonify({'success': False, 'message': 'Address not found'}), 404
        if address.is_default:
            return jsonify({'success': False, 'message': 'Cannot delete default address'}), 400
            
        db.session.delete(address)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Address deleted successfully'})
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting address: {str(e)}")
        return jsonify({'success': False, 'message': 'Error deleting address'}), 500
