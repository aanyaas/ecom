import secrets
import bcrypt
from datetime import datetime, timedelta, UTC
from flask import Blueprint, request, session, redirect, url_for, jsonify, flash, current_app
from flask_mail import Message
from extensions import db
from models import Users, OtpVerifications, Cart, GuestCart, LoyaltyLedger
from utils.validation import validate_email
from utils.limiter_shared import limiter

auth_bp = Blueprint('auth_bp', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if not username or not password:
            msg = 'Both username and password are required'
            if is_ajax:
                return jsonify({'success': False, 'error': msg}), 400
            flash(msg, 'error')
            return redirect(url_for('pages_bp.home'))
        try:
            user = db.session.scalars(db.select(Users).filter_by(username=username)).first()
            if user and bcrypt.checkpw(password.encode('utf-8'), user.password.encode('utf-8')):
                guest_id = session.get('guest_id')
                session.clear()
                session['user_id'] = user.id
                session['username'] = user.username
                if guest_id:
                    try:
                        guest_items = db.session.scalars(db.select(GuestCart).filter_by(guest_id=guest_id)).all()
                        for item in guest_items:
                            cart_item = db.session.scalars(db.select(Cart).filter_by(user_id=user.id, product_id=item.product_id)).first()
                            if cart_item:
                                cart_item.quantity += item.quantity
                            else:
                                new_cart_item = Cart(user_id=user.id, product_id=item.product_id, quantity=item.quantity)
                                db.session.add(new_cart_item)
                            db.session.delete(item)
                        db.session.commit()
                    except Exception as e:
                        db.session.rollback()
                        print(f"Error merging carts via ORM: {str(e)}")

                if is_ajax:
                    return jsonify({'success': True, 'message': 'Login successful!', 'next': request.form.get('next') or url_for('pages_bp.home')})
                flash('Login successful!', 'success')
                return redirect(request.args.get('next') or request.form.get('next') or url_for('pages_bp.home'))
            else:
                msg = 'Invalid username or password'
                if is_ajax:
                    return jsonify({'success': False, 'error': msg})
                flash(msg, 'error')
        except Exception as e:
            print(f"Login error: {str(e)}")
            msg = 'An error occurred during login'
            if is_ajax:
                return jsonify({'success': False, 'error': msg}), 500
            flash(msg, 'error')
    
    next_url = request.args.get('next')
    return redirect(url_for('pages_bp.home', login='true', next=next_url))

@auth_bp.route('/send-login-otp', methods=['POST'])
@limiter.limit("5 per minute")
def send_login_otp():
    try:
        email = request.form.get('email', '').strip()
        if not email:
            return jsonify({'success': False, 'message': 'Email is required'}), 400

        # Validate that it is a correct format of email ID
        if not validate_email(email):
            return jsonify({'success': False, 'message': 'Please enter a valid email address.'}), 400

        # Generate 6-digit OTP
        otp = str(secrets.randbelow(900000) + 100000)
        # Set expiry (5 minutes from now in UTC)
        expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)

        # Store OTP
        new_otp = OtpVerifications(email=email, otp=otp, expires_at=expires_at)
        db.session.add(new_otp)
        db.session.commit()

        # Send Email via Mail extension
        mail = current_app.extensions.get('mail')
        if mail:
            msg = Message(
                "Login OTP - Aanyaas Enterprises",
                sender=current_app.config.get('MAIL_DEFAULT_SENDER'),
                recipients=[email],
                body=f"Your login OTP is: {otp}\n\nThis code expires in 5 minutes. Do not share it with anyone."
            )
            mail.send(msg)
            return jsonify({'success': True, 'message': 'OTP sent successfully to your email.'})
        else:
            return jsonify({'success': False, 'message': 'Email service currently unavailable.'}), 500
    except Exception as e:
        db.session.rollback()
        print(f"Error sending login OTP: {str(e)}")
        return jsonify({'success': False, 'message': 'Failed to send OTP. Please try again.'}), 500

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('pages_bp.home'))


@auth_bp.route('/verify-login-otp', methods=['POST'])
def verify_login_otp():
    try:
        email = request.form.get('email', '').strip()
        otp = request.form.get('otp', '').strip()

        if not email or not otp:
            return jsonify({'success': False, 'message': 'Email and OTP are required'}), 400

        # Check OTP verification record
        otp_record = db.session.scalars(
            db.select(OtpVerifications)
            .filter(OtpVerifications.email == email)
            .filter(OtpVerifications.otp == otp)
            .filter(OtpVerifications.expires_at > datetime.now(UTC).replace(tzinfo=None))
            .order_by(OtpVerifications.created_at.desc())
        ).first()

        if not otp_record:
            return jsonify({'success': False, 'message': 'Invalid or expired OTP.'}), 400

        # Check if user exists
        user = db.session.scalars(db.select(Users).filter_by(email=email)).first()

        if not user:
            # Check for referral in session
            ref_code = session.get('referral_code')
            referred_by_id = None
            if ref_code:
                referrer = db.session.scalars(db.select(Users).filter_by(referral_code=ref_code)).first()
                if referrer:
                    referred_by_id = referrer.id

            # Auto-register user since OTP was successfully verified
            random_pwd = secrets.token_urlsafe(16)
            hashed_pwd = bcrypt.hashpw(random_pwd.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            # Generate a unique referral code
            import string
            base = ''.join(c for c in email.split('@')[0] if c.isalnum()).upper()[:4]
            random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
            new_referral_code = f"{base}{random_part}"

            user = Users(
                username=email, 
                password=hashed_pwd, 
                email=email, 
                first_name='', 
                last_name='', 
                referral_code=new_referral_code, 
                referred_by=referred_by_id
            )
            db.session.add(user)
            db.session.commit()
            
            # Make sure it's fully unique
            user.referral_code = f"{base}{user.id}{random_part}"
            db.session.commit()

            # If referred, reward both users with 100 points
            if referred_by_id:
                bonus1 = LoyaltyLedger(user_id=referred_by_id, points=100, transaction_type='referral_bonus')
                bonus2 = LoyaltyLedger(user_id=user.id, points=100, transaction_type='signup_bonus')
                db.session.add_all([bonus1, bonus2])
                db.session.commit()

        guest_id = session.get('guest_id') or request.cookies.get('guest_id')

        session.clear()
        session['user_id'] = user.id
        session['username'] = user.username
        session['email'] = user.email

        # Merge guest cart if applicable
        if guest_id:
            try:
                guest_items = db.session.scalars(db.select(GuestCart).filter_by(guest_id=guest_id)).all()
                for item in guest_items:
                    cart_item = db.session.scalars(db.select(Cart).filter_by(user_id=user.id, product_id=item.product_id)).first()
                    if cart_item:
                        cart_item.quantity += item.quantity
                    else:
                        new_cart_item = Cart(user_id=user.id, product_id=item.product_id, quantity=item.quantity)
                        db.session.add(new_cart_item)
                    db.session.delete(item)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error merging carts during email OTP verification via ORM: {str(e)}")

        # Delete used OTP
        db.session.delete(otp_record)
        db.session.commit()

        return jsonify({'success': True, 'message': 'Login successful!', 'next': request.form.get('next')})
    except Exception as e:
        db.session.rollback()
        print(f"Error verifying Email OTP via ORM: {str(e)}")
        return jsonify({'success': False, 'message': 'Verification failed. Please try again.'}), 500
