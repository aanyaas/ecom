"""
blueprints/pages.py

Handles content/marketing pages and utility endpoints:
  - Home page (with cache)
  - Contact form
  - Newsletter subscription
  - Testimonials
  - Static policy pages (terms, faq, privacy, shipping, return)
  - Instagram feed API
  - Pincode/state/city lookup helpers
  - Wishlist
  - robots.txt
"""

import os
import uuid
import secrets
import traceback
from datetime import datetime

import requests
from PIL import Image
from flask import (Blueprint, current_app, make_response, render_template,
                   request, session, redirect, url_for, jsonify, flash, send_file)
from flask_mail import Message
from markupsafe import escape

from config_manager import get_config
from utils.validation import validate_email
from utils.session_helpers import get_or_create_guest_session
from utils.limiter_shared import limiter
from utils.cache_shared import cache
from extensions import db
from models import Products, CustomerTestimonials, Wishlists, WishlistItems, ProductReviews, Cart, GuestCart, Categories, CompanyInfo, Subscribers, PincodeStateCity
from sqlalchemy.sql import func, desc, and_

pages_bp = Blueprint('pages_bp', __name__)



# ---------------------------------------------------------------------------
# Cached helper: category counts + testimonials (5-minute TTL)
# ---------------------------------------------------------------------------

@cache.cached(timeout=300, key_prefix='homepage_static')
def _get_homepage_static():
    """Returns (category_counts dict, testimonials list). Cached for 5 min."""
    try:
        category_res = db.session.execute(
            db.select(Categories.slug, Categories.name, func.count(Products.id).label('count'))
            .outerjoin(Products, Products.category == Categories.slug)
            .filter(Categories.parent_id != None, Categories.is_active == 1)
            .group_by(Categories.id, Categories.slug, Categories.name)
        ).all()
        category_counts = {row.slug: row.count for row in category_res}

        testimonials_res = db.session.execute(
            db.select(CustomerTestimonials.customer_name, CustomerTestimonials.city, CustomerTestimonials.rating, CustomerTestimonials.feedback, CustomerTestimonials.customer_photo)
            .filter(CustomerTestimonials.is_approved == 1)
            .order_by(desc(CustomerTestimonials.created_at))
            .limit(8)
        ).all()
        testimonials = [dict(row._mapping) for row in testimonials_res]

        return category_counts, testimonials
    except Exception:
        return {}, []

@cache.cached(timeout=60, key_prefix='homepage_products')
def _get_homepage_products():
    """Returns the latest 8 active products. Cached for 60 seconds."""
    try:
        query = db.select(Products).filter_by(is_active=1).order_by(Products.id.desc()).limit(8)
        products_objs = db.session.scalars(query).all()
        
        products = []
        for p in products_objs:
            product_dict = {
                'id': p.id,
                'name': p.name,
                'price': p.price,
                'image': p.image,
                'stock_quantity': p.stock_quantity,
                'mrp': p.mrp
            }
            if p.mrp and p.mrp > p.price:
                product_dict['discount'] = round((p.mrp - p.price) / p.mrp * 100)
            else:
                product_dict['discount'] = 0
            products.append(product_dict)
            
        return products
    except Exception as e:
        print(f"Error in ORM _get_homepage_products: {e}")
        return []


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@pages_bp.route('/')
def home():
    try:
        # Cached: categories + testimonials (5-min TTL, no per-request DB hit)
        category_counts, testimonials = _get_homepage_static()

        # Cached: latest 8 active products (60-sec TTL)
        products = _get_homepage_products()

        # Always-fresh: cart state is session-specific
        cart_product_ids = []
        if 'user_id' in session:
            cart_items = db.session.scalars(db.select(Cart.product_id).filter_by(user_id=session['user_id'])).all()
            cart_product_ids = list(cart_items)
        elif 'guest_id' in session:
            cart_items = db.session.scalars(db.select(GuestCart.product_id).filter_by(guest_id=session['guest_id'])).all()
            cart_product_ids = list(cart_items)

        from utils.session_helpers import get_guest_or_user_cart_count

        # Load JSON-driven UI configuration parameters
        ui_banners = get_config('HOME_BANNERS', [])
        ui_featured_categories = get_config('HOME_CATEGORIES', ['necklaces', 'earrings', 'bags', 'hair'])
        ui_offers = get_config('HOME_OFFERS', [])

        return render_template(
            'index.html',
            user_logged_in='user_id' in session,
            username=session.get('username', 'Guest'),
            cart_count=get_guest_or_user_cart_count(),
            category_counts=category_counts,
            products=products,
            cart_product_ids=cart_product_ids,
            testimonials=testimonials,
            ui_banners=ui_banners,
            ui_featured_categories=ui_featured_categories,
            ui_offers=ui_offers
        )
    except Exception as e:
        print(f"Home page error: {e}")
        category_counts = {cat: 0 for cat in [
            'necklaces', 'chokers', 'earrings', 'bracelets',
            'hair', 'bags', 'decorative', 'festive', 'others'
        ]}
        return render_template(
            'index.html',
            user_logged_in='user_id' in session,
            username=session.get('username', 'Guest'),
            cart_count=0,
            category_counts=category_counts,
            products=[],
            cart_product_ids=[],
            testimonials=[]
        )

# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------

@pages_bp.route('/contact', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def contact():
    if request.method == 'POST':
        name = escape(request.form.get('name', '').strip())
        sender_email = request.form.get('email', '').strip()
        subject = escape(request.form.get('subject', '').strip())
        message = escape(request.form.get('message', '').strip())
        if not name or not sender_email or not subject or not message:
            flash('Please fill in all fields.', 'warning')
            return render_template('contact.html')
        if not validate_email(sender_email):
            flash('Please enter a valid email address.', 'error')
            return render_template('contact.html')
        try:
            company_obj = db.session.scalars(db.select(CompanyInfo).limit(1)).first()
            if company_obj:
                company = {
                    'company_name': company_obj.company_name,
                    'phone': company_obj.phone,
                    'company_email': company_obj.email,
                    'address': company_obj.address,
                    'city': company_obj.city,
                    'state': company_obj.state
                }
            else:
                company = {}
        except Exception:
            company = {}
        company_name = company.get('company_name', 'Aanyaas Enterprises')
        company_phone = company.get('phone', '+91 9555144442')
        company_email_addr = company.get('company_email', os.getenv('MAIL_USERNAME'))
        site_url = url_for('pages_bp.home', _external=True)
        received_on = datetime.now().strftime("%d %b %Y at %I:%M %p")
        try:
            admin_html = f'''
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<style>
  body{{margin:0;padding:0;background:#f8f1ec;font-family:Georgia,serif;}}
  .wrap{{max-width:600px;margin:30px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.1);}}
  .hdr{{background:linear-gradient(135deg,#ba6286,#8b4563);padding:30px;text-align:center;}}
  .hdr h1{{color:#fff;margin:0;font-size:22px;letter-spacing:2px;font-weight:normal;}}
  .body{{padding:30px 40px;}}
  .label{{font-size:11px;color:#ba6286;text-transform:uppercase;letter-spacing:1px;margin-top:16px;margin-bottom:4px;font-weight:bold;}}
  .value{{font-size:14px;color:#3a2030;background:#fdf6f0;border-left:3px solid #ba6286;padding:10px 14px;border-radius:4px;}}
  .msg-box{{font-size:14px;color:#3a2030;background:#fdf6f0;border-left:3px solid #ba6286;padding:14px;border-radius:4px;line-height:1.7;white-space:pre-wrap;}}
  .ftr{{background:#5c2a3e;padding:18px;text-align:center;}}
  .ftr p{{color:#f5d6e8;font-size:11px;margin:3px 0;}}
</style></head><body>
<div class="wrap">
  <div class="hdr"><h1>&#x1F4E9; New Contact Message</h1></div>
  <div class="body">
    <p style="color:#5c2a3e;font-size:15px;">You have received a new enquiry through your website contact form.</p>
    <div class="label">Name</div><div class="value">{name}</div>
    <div class="label">Email</div><div class="value"><a href="mailto:{sender_email}" style="color:#ba6286;">{sender_email}</a></div>
    <div class="label">Subject</div><div class="value">{subject}</div>
    <div class="label">Message</div><div class="msg-box">{message}</div>
    <p style="margin-top:24px;font-size:12px;color:#a08090;">Received on: {received_on}</p>
  </div>
  <div class="ftr"><p>{company_name} | {company_phone}</p><p><a href="{site_url}" style="color:#f5d6e8;">{site_url}</a></p></div>
</div></body></html>'''

            mail = current_app.extensions.get('mail')
            if mail:
                admin_msg = Message(
                    subject=f"{company_name} - New Enquiry: {subject}",
                    recipients=[company_email_addr]
                )
                admin_msg.html = admin_html
                mail.send(admin_msg)

                reply_html = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Thank you for contacting {company_name}</title>
<style>
  body{{margin:0;padding:0;background:#f8f1ec;font-family:Georgia,serif;}}
  .wrap{{max-width:600px;margin:30px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.1);}}
  .hdr{{background:linear-gradient(135deg,#ba6286,#8b4563);padding:35px 30px;text-align:center;}}
  .hdr h1{{color:#fff;margin:0;font-size:22px;letter-spacing:2px;font-weight:normal;}}
  .hero{{padding:30px 40px;text-align:center;background:#fdf6f0;border-bottom:2px solid #f0ddd6;}}
  .hero h2{{color:#5c2a3e;font-size:20px;margin:0 0 10px;}}
  .hero p{{color:#7a4a5a;font-size:14px;line-height:1.7;margin:0;}}
  .cta{{text-align:center;padding:10px 40px 28px;}}
  .cta a{{display:inline-block;background:linear-gradient(135deg,#ba6286,#8b4563);color:#fff;text-decoration:none;padding:12px 32px;border-radius:30px;font-size:14px;letter-spacing:1px;}}
  .ftr{{background:#5c2a3e;padding:22px 30px;text-align:center;}}
  .ftr p{{color:#f5d6e8;font-size:11px;margin:3px 0;line-height:1.6;}}
  .ftr a{{color:#f5d6e8;text-decoration:none;}}
</style></head><body>
<div class="wrap">
  <div class="hdr"><h1>{company_name}</h1><p>&#x2728; Handmade with Love &#x2728;</p></div>
  <div class="hero">
    <h2>&#x2705; Message Received!</h2>
    <p>Dear <strong>{name}</strong>, thank you for reaching out to us.<br>We have received your message and our team will get back to you within <strong>24-48 hours</strong>.</p>
  </div>
  <div class="cta"><a href="{site_url}">&#x1F6CD; Continue Shopping</a></div>
  <div class="ftr">
    <p><strong style="color:#fff;font-size:13px;">{company_name}</strong></p>
    <p>&#x1F4DE; {company_phone} &nbsp;|&nbsp; &#x2709; <a href="mailto:{company_email_addr}">{company_email_addr}</a></p>
    <p><a href="{site_url}">&#x1F310; {site_url}</a></p>
  </div>
</div></body></html>'''

                reply_msg = Message(
                    subject=f"We received your message  -  {company_name}",
                    recipients=[sender_email]
                )
                reply_msg.html = reply_html
                mail.send(reply_msg)
            flash('Your message has been sent successfully! We will get back to you within 24-48 hours.', 'success')
        except Exception as e:
            print(f"Contact email error: {e}")
            flash('Message received! However, confirmation email could not be sent.', 'info')
        return redirect(url_for('pages_bp.contact'))
    return render_template('contact.html')


# ---------------------------------------------------------------------------
# Subscribe
# ---------------------------------------------------------------------------

@pages_bp.route('/subscribe', methods=['POST'])
@limiter.limit("5 per minute")
def subscribe():
    try:
        email = request.form.get('email')
        if not email or not validate_email(email):
            flash('Please enter a valid email address', 'error')
            return redirect(url_for('pages_bp.home'))

        try:
            new_sub = Subscribers(email=email)
            db.session.add(new_sub)
            db.session.commit()
        except Exception:
            db.session.rollback()
            # Already subscribed or other integrity error
            pass

        try:
            company_obj = db.session.scalars(db.select(CompanyInfo).limit(1)).first()
            if company_obj:
                company = {
                    'company_name': company_obj.company_name,
                    'phone': company_obj.phone,
                    'company_email': company_obj.email,
                    'address': company_obj.address,
                    'city': company_obj.city,
                    'state': company_obj.state
                }
            else:
                company = {}
        except Exception:
            company = {}
        
        company_name = company.get('company_name', 'Aanyaas Enterprises')
        company_phone = company.get('phone', '+91 9555144442')
        company_email = company.get('company_email', 'aanyaasenterprises@gmail.com')
        company_address = company.get('address', '')
        company_city = company.get('city', '')
        company_state = company.get('state', '')
        site_url = url_for('pages_bp.home', _external=True)
        try:
            html_body = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Welcome to {company_name} Newsletter</title>
<style>
  body {{ margin:0; padding:0; background:#f8f1ec; font-family: Georgia, serif; }}
  .wrapper {{ max-width:620px; margin:30px auto; background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 4px 20px rgba(0,0,0,0.1); }}
  .header {{ background: linear-gradient(135deg, #ba6286 0%, #8b4563 100%); padding:40px 30px; text-align:center; }}
  .header h1 {{ color:#fff; margin:0; font-size:26px; letter-spacing:2px; font-weight:normal; }}
  .hero {{ background:#fdf6f0; padding:35px 40px; text-align:center; border-bottom:3px solid #f0ddd6; }}
  .hero h2 {{ color:#5c2a3e; font-size:22px; margin:0 0 10px; }}
  .hero p {{ color:#7a4a5a; font-size:15px; line-height:1.7; margin:0; }}
  .cta {{ text-align:center; padding:10px 40px 35px; }}
  .cta-btn {{ display:inline-block; background:linear-gradient(135deg, #ba6286, #8b4563); color:#ffffff; text-decoration:none; padding:14px 36px; border-radius:30px; font-size:15px; letter-spacing:1px; }}
  .footer {{ background:#5c2a3e; padding:25px 30px; text-align:center; }}
  .footer p {{ color:#f5d6e8; font-size:12px; margin:4px 0; line-height:1.6; }}
  .footer a {{ color:#f5d6e8; text-decoration:none; }}
</style></head><body>
<div class="wrapper">
  <div class="header"><h1>{company_name}</h1><p>&#x2728; Handmade with Love &#x2728;</p></div>
  <div class="hero">
    <h2>Welcome to Our Creative Family! &#x1F33A;</h2>
    <p>Thank you for subscribing to the <strong>{company_name}</strong> newsletter.<br>
    You are now part of an exclusive circle of craft lovers who appreciate the beauty of handmade &amp; handcrafted artisan products.</p>
  </div>
  <div class="cta"><a href="{site_url}" class="cta-btn">&#x1F6CD; Explore Our Collection</a></div>
  <div class="footer">
    <p><strong style="color:#fff; font-size:14px;">{company_name}</strong></p>
    <p>{company_address}{", " + company_city if company_city else ""}{", " + company_state if company_state else ""}</p>
    <p>&#x1F4DE; {company_phone} &nbsp;|&nbsp; &#x2709; <a href="mailto:{company_email}">{company_email}</a></p>
    <p><a href="{site_url}">&#x1F310; Visit our Website</a></p>
  </div>
</div></body></html>'''
            mail = current_app.extensions.get('mail')
            if mail:
                msg = Message(
                    subject=f"Welcome to {company_name}  -  Thank You for Subscribing! \U0001F33A",
                    recipients=[email]
                )
                msg.html = html_body
                mail.send(msg)
        except Exception as e:
            print(f"Error sending subscription email: {e}")
        
        flash('Thank you for subscribing!', 'success')
        return redirect(url_for('pages_bp.home'))
    except Exception as e:
        flash('Subscription failed due to unexpected error', 'error')
        print(f"Unexpected error during subscription: {e}")
        return redirect(url_for('pages_bp.home'))


# ---------------------------------------------------------------------------
# Static policy pages
# ---------------------------------------------------------------------------

@pages_bp.route('/terms')
def terms():
    return render_template('terms.html')


@pages_bp.route('/faq')
def faq():
    return render_template('faq.html',
                           user_logged_in='user_id' in session,
                           username=session.get('username'))


@pages_bp.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html',
                           user_logged_in='user_id' in session,
                           username=session.get('username'))


@pages_bp.route('/shipping-policy')
def shipping_policy():
    return render_template('shipping_policy.html',
                           user_logged_in='user_id' in session,
                           username=session.get('username'))


@pages_bp.route('/return-policy')
def return_policy():
    return render_template('return_policy.html',
                           user_logged_in='user_id' in session,
                           username=session.get('username'))


# ---------------------------------------------------------------------------
# Testimonials
# ---------------------------------------------------------------------------

@pages_bp.route('/testimonials')
def testimonials():
    """Public testimonials page — shows approved feedback."""
    try:
        query = db.select(CustomerTestimonials).filter_by(is_approved=1).order_by(CustomerTestimonials.created_at.desc()).limit(50)
        testimonials_objs = db.session.scalars(query).all()
        
        testimonials_list = []
        for t in testimonials_objs:
            testimonials_list.append({
                'customer_name': t.customer_name,
                'city': t.city,
                'rating': t.rating,
                'feedback': t.feedback,
                'created_at': t.created_at,
                'customer_photo': t.customer_photo
            })
            
        return render_template('testimonials.html',
                               testimonials=testimonials_list,
                               form_data=None,
                               user_logged_in='user_id' in session,
                               username=session.get('username'))
    except Exception as e:
        print(f"Error loading testimonials via ORM: {e}")
        traceback.print_exc()
        return render_template('testimonials.html',
                               testimonials=[],
                               form_data=None,
                               user_logged_in='user_id' in session,
                               username=session.get('username'))


@pages_bp.route('/testimonials/submit', methods=['POST'])
def submit_testimonial():
    """Handle customer feedback form submission."""
    customer_name = request.form.get('customer_name', '').strip()
    email = request.form.get('email', '').strip()
    city = request.form.get('city', '').strip()
    rating = request.form.get('rating', '5')
    feedback = request.form.get('feedback', '').strip()

    form_data = {'customer_name': customer_name, 'email': email,
                 'city': city, 'rating': rating, 'feedback': feedback}

    if not customer_name or not feedback:
        flash('Please fill in your name and feedback.', 'error')
        try:
            query = db.select(CustomerTestimonials).filter_by(is_approved=1).order_by(CustomerTestimonials.created_at.desc()).limit(50)
            testimonials_objs = db.session.scalars(query).all()
            testimonials_list = []
            for t in testimonials_objs:
                testimonials_list.append({
                    'customer_name': t.customer_name,
                    'city': t.city,
                    'rating': t.rating,
                    'feedback': t.feedback,
                    'created_at': t.created_at,
                    'customer_photo': t.customer_photo
                })
        except Exception:
            testimonials_list = []
        return render_template('testimonials.html',
                               testimonials=testimonials_list,
                               form_data=form_data,
                               user_logged_in='user_id' in session,
                               username=session.get('username'))

    try:
        rating_int = max(1, min(5, int(rating)))
    except (ValueError, TypeError):
        rating_int = 5

    photo_filename = None
    photo_file = request.files.get('customer_photo')
    if photo_file and photo_file.filename:
        allowed_ext = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
        ext = photo_file.filename.rsplit('.', 1)[-1].lower() if '.' in photo_file.filename else ''
        if ext in allowed_ext:
            photo_filename = f"testi_{uuid.uuid4().hex[:10]}.jpg"
            upload_dir = os.path.join(current_app.root_path, 'static', 'img', 'testimonials')
            os.makedirs(upload_dir, exist_ok=True)
            try:
                img = Image.open(photo_file.stream)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                max_size = (800, 800)
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                img.save(os.path.join(upload_dir, photo_filename), "JPEG", quality=70, optimize=True)
            except Exception as img_err:
                print(f"Error optimizing image, saving original: {img_err}")
                photo_file.seek(0)
                photo_file.save(os.path.join(upload_dir, photo_filename))

    try:
        new_testimonial = CustomerTestimonials(
            customer_name=customer_name[:80],
            email=email[:120] if email else None,
            city=city[:60] if city else None,
            rating=rating_int,
            feedback=feedback[:1000],
            customer_photo=photo_filename,
            is_approved=0
        )
        db.session.add(new_testimonial)
        db.session.commit()
        flash('Thank you for your feedback! It will appear after a quick review.', 'success')
        return redirect(url_for('pages_bp.testimonials'))
    except Exception as e:
        db.session.rollback()
        print(f"Error saving testimonial: {e}")
        traceback.print_exc()
        flash('Sorry, there was an error saving your feedback. Please try again.', 'error')
        return redirect(url_for('pages_bp.testimonials'))


# ---------------------------------------------------------------------------
# Instagram API
# ---------------------------------------------------------------------------

@pages_bp.route('/api/instagram')
def get_instagram_feed():
    """Fetch latest media from Instagram Basic Display API."""
    access_token = current_app.config.get('INSTAGRAM_ACCESS_TOKEN')
    if not access_token or access_token == 'your_instagram_token_here':
        return jsonify({'success': False, 'message': 'Instagram Access Token not configured.'})

    try:
        media_count = get_config('INSTAGRAM_MEDIA_COUNT', 6)
        url = (
            f"https://graph.instagram.com/me/media"
            f"?fields=id,caption,media_type,media_url,permalink,thumbnail_url,timestamp"
            f"&access_token={access_token}&limit={media_count}"
        )
        response = requests.get(url, timeout=10)
        data = response.json()

        if 'error' in data:
            print(f"Instagram API Error: {data['error'].get('message')}")
            return jsonify({'success': False, 'message': data['error'].get('message')})

        return jsonify({'success': True, 'data': data.get('data', [])})
    except Exception as e:
        print(f"Error fetching Instagram feed: {e}")
        return jsonify({'success': False, 'message': str(e)})


# ---------------------------------------------------------------------------
# Pincode / State / City helpers
# ---------------------------------------------------------------------------

@pages_bp.route('/get-pincode-details', methods=['POST'])
def get_pincode_details():
    try:
        pincode = request.form.get('pincode', '').strip()
        if not pincode or len(pincode) != 6 or not pincode.isdigit():
            return jsonify({'success': False, 'message': 'Invalid pincode format', 'states': []}), 400
        
        result = db.session.execute(
            db.select(PincodeStateCity.city, PincodeStateCity.state_name.label('state'), PincodeStateCity.state_code)
            .filter_by(pincode=pincode)
            .limit(1)
        ).first()
        
        if result:
            cities_res = db.session.scalars(
                db.select(PincodeStateCity.city).filter_by(state_name=result.state).distinct().order_by(PincodeStateCity.city)
            ).all()
            cities = list(cities_res)
            return jsonify({'success': True, 'city': result.city, 'state': result.state,
                            'state_code': result.state_code, 'cities': cities})
        else:
            states_res = db.session.scalars(
                db.select(PincodeStateCity.state_name).distinct().order_by(PincodeStateCity.state_name)
            ).all()
            states = list(states_res)
            return jsonify({'success': False, 'message': 'No address found for this pincode', 'states': states})
    except Exception as e:
        print(f"Error fetching pincode details: {str(e)}")
        return jsonify({'success': False, 'message': 'Error fetching pincode details', 'states': []}), 500


@pages_bp.route('/get-state-code', methods=['POST'])
def get_state_code():
    try:
        state = request.form.get('state', '').strip()
        if not state:
            return jsonify({'success': False, 'state_code': ''}), 400
        result = db.session.scalars(
            db.select(PincodeStateCity.state_code).filter_by(state_name=state).distinct().limit(1)
        ).first()
        if result:
            return jsonify({'success': True, 'state_code': result})
        else:
            return jsonify({'success': False, 'state_code': ''})
    except Exception as e:
        print(f"Error fetching state code: {str(e)}")
        return jsonify({'success': False, 'state_code': ''}), 500


@pages_bp.route('/get-cities-by-state', methods=['POST'])
def get_cities_by_state():
    try:
        state = request.form.get('state', '').strip()
        if not state:
            return jsonify({'success': False, 'cities': []}), 400
        cities_res = db.session.scalars(
            db.select(PincodeStateCity.city).filter_by(state_name=state).distinct().order_by(PincodeStateCity.city)
        ).all()
        cities = list(cities_res)
        return jsonify({'success': True, 'cities': cities})
    except Exception as e:
        print(f"Error fetching cities: {str(e)}")
        return jsonify({'success': False, 'cities': []}), 500


# ---------------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------------

def _get_or_create_wishlist_id(user_id, session_id):
    if user_id:
        wishlist = db.session.scalars(db.select(Wishlists).filter_by(user_id=user_id)).first()
        if wishlist:
            # If guest session has a wishlist, merge it
            if session_id:
                guest_wishlist = db.session.scalars(db.select(Wishlists).filter_by(session_id=session_id)).first()
                if guest_wishlist:
                    guest_items = db.session.scalars(db.select(WishlistItems).filter_by(wishlist_id=guest_wishlist.id)).all()
                    for item in guest_items:
                        # Add item to user wishlist if it doesn't already exist
                        existing = db.session.scalars(db.select(WishlistItems).filter_by(wishlist_id=wishlist.id, product_id=item.product_id)).first()
                        if not existing:
                            item.wishlist_id = wishlist.id
                            db.session.add(item)
                    db.session.delete(guest_wishlist)
                    db.session.commit()
            return wishlist.id
        else:
            share_token = secrets.token_urlsafe(16)
            wishlist = Wishlists(user_id=user_id, share_token=share_token)
            db.session.add(wishlist)
            db.session.commit()
            return wishlist.id
    else:
        wishlist = db.session.scalars(db.select(Wishlists).filter_by(session_id=session_id)).first()
        if wishlist:
            return wishlist.id
        else:
            share_token = secrets.token_urlsafe(16)
            wishlist = Wishlists(session_id=session_id, share_token=share_token)
            db.session.add(wishlist)
            db.session.commit()
            return wishlist.id


@pages_bp.route('/wishlist')
def wishlist():
    guest_id = get_or_create_guest_session()
    user_id = session.get('user_id')
    try:
        wishlist_id = _get_or_create_wishlist_id(user_id, guest_id)

        # Query wishlist items
        items_query = db.session.query(
            Products.id, Products.name, Products.price, Products.mrp, Products.image,
            Products.stock_quantity, Products.sku, WishlistItems.added_at,
            db.func.coalesce(db.func.avg(ProductReviews.rating), 0).label('avg_rating'),
            db.func.coalesce(db.func.count(ProductReviews.id), 0).label('review_count')
        ).join(
            WishlistItems, WishlistItems.product_id == Products.id
        ).outerjoin(
            ProductReviews, ProductReviews.product_id == Products.id
        ).filter(
            WishlistItems.wishlist_id == wishlist_id,
            Products.is_active == True
        ).group_by(
            Products.id, Products.name, Products.price, Products.mrp, Products.image,
            Products.stock_quantity, Products.sku, WishlistItems.added_at
        ).order_by(WishlistItems.added_at.desc())

        items_res = items_query.all()
        items = [dict(row._mapping) for row in items_res]

        wishlist = db.session.get(Wishlists, wishlist_id)
        share_token = wishlist.share_token if wishlist else None

        cart_product_ids = []
        if user_id:
            cart_product_ids = [item.product_id for item in db.session.scalars(db.select(Cart).filter_by(user_id=user_id)).all()]
        elif guest_id:
            cart_product_ids = [item.product_id for item in db.session.scalars(db.select(GuestCart).filter_by(guest_id=guest_id)).all()]

        resp = make_response(render_template(
            'wishlist.html', items=items, share_token=share_token,
            is_shared=False, user_logged_in='user_id' in session,
            username=session.get('username'), cart_product_ids=cart_product_ids
        ))
        if guest_id and not user_id:
            resp.set_cookie('guest_id', guest_id, max_age=86400 * 30, samesite='Lax', httponly=True)
        return resp
    except Exception as e:
        db.session.rollback()
        print(f"Error loading wishlist: {e}")
        return redirect(url_for('pages_bp.home'))

@pages_bp.route('/wishlist/shared/<share_token>')
def shared_wishlist(share_token):
    try:
        wishlist = db.session.scalars(db.select(Wishlists).filter_by(share_token=share_token)).first()
        if not wishlist:
            flash('Wishlist not found or link has expired.', 'error')
            return redirect(url_for('pages_bp.home'))

        wishlist_id = wishlist.id
        
        # Query wishlist items
        items_query = db.session.query(
            Products.id, Products.name, Products.price, Products.mrp, Products.image,
            Products.stock_quantity, Products.sku, WishlistItems.added_at,
            db.func.coalesce(db.func.avg(ProductReviews.rating), 0).label('avg_rating'),
            db.func.coalesce(db.func.count(ProductReviews.id), 0).label('review_count')
        ).join(
            WishlistItems, WishlistItems.product_id == Products.id
        ).outerjoin(
            ProductReviews, ProductReviews.product_id == Products.id
        ).filter(
            WishlistItems.wishlist_id == wishlist_id,
            Products.is_active == True
        ).group_by(
            Products.id, Products.name, Products.price, Products.mrp, Products.image,
            Products.stock_quantity, Products.sku, WishlistItems.added_at
        ).order_by(WishlistItems.added_at.desc())

        items_res = items_query.all()
        items = [dict(row._mapping) for row in items_res]

        guest_id = session.get('guest_id') or request.cookies.get('guest_id')
        user_id = session.get('user_id')

        cart_product_ids = []
        if user_id:
            cart_product_ids = [item.product_id for item in db.session.scalars(db.select(Cart).filter_by(user_id=user_id)).all()]
        elif guest_id:
            cart_product_ids = [item.product_id for item in db.session.scalars(db.select(GuestCart).filter_by(guest_id=guest_id)).all()]

        is_owner = bool(user_id and wishlist.user_id == user_id)

        return render_template(
            'wishlist.html', items=items, share_token=share_token,
            is_shared=True, is_owner=is_owner,
            user_logged_in='user_id' in session,
            username=session.get('username'), cart_product_ids=cart_product_ids
        )
    except Exception as e:
        db.session.rollback()
        print(f"Error loading shared wishlist: {e}")
        return redirect(url_for('pages_bp.home'))


@pages_bp.route('/wishlist/add/<int:product_id>', methods=['POST'])
def add_to_wishlist(product_id):
    guest_id = get_or_create_guest_session()
    user_id = session.get('user_id')
    try:
        wishlist_id = _get_or_create_wishlist_id(user_id, guest_id)

        existing = db.session.scalars(db.select(WishlistItems).filter_by(wishlist_id=wishlist_id, product_id=product_id)).first()
        if existing:
            return jsonify({'success': True, 'message': 'Already in wishlist'})

        new_item = WishlistItems(wishlist_id=wishlist_id, product_id=product_id)
        db.session.add(new_item)
        db.session.commit()

        resp = jsonify({'success': True, 'message': 'Added to wishlist'})
        if guest_id and not user_id:
            resp.set_cookie('guest_id', guest_id, max_age=86400 * 30, samesite='Lax', httponly=True)
        return resp
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@pages_bp.route('/wishlist/remove/<int:product_id>', methods=['POST'])
def remove_from_wishlist(product_id):
    guest_id = session.get('guest_id') or request.cookies.get('guest_id') if 'user_id' not in session else None
    user_id = session.get('user_id')
    try:
        if user_id:
            wishlist = db.session.scalars(db.select(Wishlists).filter_by(user_id=user_id)).first()
        else:
            if not guest_id:
                return jsonify({'success': False, 'message': 'Not found'})
            wishlist = db.session.scalars(db.select(Wishlists).filter_by(session_id=guest_id)).first()

        if not wishlist:
            return jsonify({'success': False, 'message': 'Wishlist not found'})

        item = db.session.scalars(db.select(WishlistItems).filter_by(wishlist_id=wishlist.id, product_id=product_id)).first()
        if item:
            db.session.delete(item)
            db.session.commit()
            
        return jsonify({'success': True, 'message': 'Removed from wishlist'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@pages_bp.route('/wishlist/add_all_to_cart', methods=['POST'])
def add_all_to_cart():
    guest_id = session.get('guest_id') or request.cookies.get('guest_id') if 'user_id' not in session else None
    user_id = session.get('user_id')
    try:
        if user_id:
            wishlist = db.session.scalars(db.select(Wishlists).filter_by(user_id=user_id)).first()
        else:
            if not guest_id:
                return jsonify({'success': False, 'message': 'Not found'})
            wishlist = db.session.scalars(db.select(Wishlists).filter_by(session_id=guest_id)).first()

        if not wishlist:
            return jsonify({'success': False, 'message': 'Wishlist not found'})

        wishlist_id = wishlist.id

        items = db.session.query(
            WishlistItems.product_id, Products.stock_quantity
        ).join(
            Products, Products.id == WishlistItems.product_id
        ).filter(
            WishlistItems.wishlist_id == wishlist_id,
            Products.is_active == True
        ).all()

        added_count = 0
        for item in items:
            prod_id = item.product_id
            stock = item.stock_quantity
            if stock > 0:
                if user_id:
                    existing = db.session.scalars(db.select(Cart).filter_by(user_id=user_id, product_id=prod_id)).first()
                else:
                    existing = db.session.scalars(db.select(GuestCart).filter_by(guest_id=guest_id, product_id=prod_id)).first()
                    
                if existing:
                    new_qty = existing.quantity + 1
                    if new_qty <= stock:
                        existing.quantity = new_qty
                        added_count += 1
                else:
                    if user_id:
                        new_cart_item = Cart(user_id=user_id, product_id=prod_id, quantity=1)
                    else:
                        new_cart_item = GuestCart(guest_id=guest_id, product_id=prod_id, quantity=1)
                    db.session.add(new_cart_item)
                    added_count += 1

        db.session.commit()
        return jsonify({'success': True, 'message': f'Added {added_count} items to cart'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

@pages_bp.route('/robots.txt')
def robots():
    return send_file(os.path.join(str(current_app.static_folder), 'robots.txt'))

# ---------------------------------------------------------------------------
# Sitemap.xml
# ---------------------------------------------------------------------------
@pages_bp.route('/sitemap.xml')
@cache.cached(timeout=3600)
def sitemap():
    static_pages = [
        {'loc': url_for('pages_bp.home', _external=True), 'changefreq': 'daily', 'priority': '1.0'},
        {'loc': url_for('shop_bp.shop', _external=True), 'changefreq': 'daily', 'priority': '0.9'},
        {'loc': url_for('pages_bp.contact', _external=True), 'changefreq': 'monthly', 'priority': '0.5'},
        {'loc': url_for('pages_bp.faq', _external=True), 'changefreq': 'monthly', 'priority': '0.5'}
    ]
    
    categories = db.session.scalars(db.select(Categories).filter_by(is_active=1)).all()
    products = db.session.scalars(db.select(Products).filter_by(is_active=1)).all()
    
    sitemap_xml = render_template('sitemap.xml', 
                                  static_pages=static_pages,
                                  categories=categories,
                                  products=products)
    
    response = make_response(sitemap_xml)
    response.headers['Content-Type'] = 'application/xml'
    return response
