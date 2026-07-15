import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import render_template, request, redirect, url_for, flash, session, jsonify, g
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
from flask_wtf import FlaskForm
from wtforms import Form, StringField, PasswordField, validators, DecimalField, IntegerField, SelectField, TextAreaField, BooleanField
from wtforms.fields import DateField
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import current_app
import os
import uuid
import mysql.connector
from dotenv import load_dotenv
from functools import wraps
from PIL import Image, ImageDraw, ImageFont
from user_agents import parse  # pip install pyyaml ua-parser user-agents
import requests
import json
import re
from utils.limiter_shared import limiter

try:
    PHONEPE_REFUND_AVAILABLE = True
except ImportError:
    PHONEPE_REFUND_AVAILABLE = False

# Load environment variables
load_dotenv()

# Initialize extensions
bcrypt = Bcrypt()
csrf = CSRFProtect()

# Create admin blueprint
admin_bp = Blueprint(
    'admin_bp',
    __name__,
    template_folder='templates/admin',
    static_folder='static/admin'
)

@admin_bp.context_processor
def inject_menus():
    from models import AdminRoles, AdminMenus, AdminRoleMenus
    from extensions import db
    
    menus = []
    if session.get('admin_logged_in') and 'admin_role' in session:
        role_name = session.get('admin_role')
        if hasattr(role_name, 'value'):
            role_name = role_name.value
            
        role = db.session.query(AdminRoles).filter_by(name=str(role_name).lower()).first()
        if role:
            # Get menus user has view permission for
            role_menus = db.session.query(AdminMenus).join(AdminRoleMenus).filter(
                AdminRoleMenus.role_id == role.id,
                AdminRoleMenus.can_view == 1,
                AdminMenus.is_active == 1
            ).order_by(AdminMenus.sort_order.asc()).all()
            menus = role_menus
            
    return dict(admin_sidebar_menus=menus)


def admin_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            flash('Please log in to access this page.', 'danger')
            return redirect(url_for('admin_bp.admin_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# Scheduled session maintenance function must be defined before use
def scheduled_session_maintenance():
    """Perform regular session maintenance tasks"""
    try:
        print("Running scheduled session maintenance...")
        # Clean up old sessions (older than 30 days)
        from admin.routes.users import cleanup_old_sessions
        cleanup_old_sessions(30)

        # Refresh geolocation for recent sessions with unknown data
        refresh_geolocation_data()
        print("Session maintenance completed")
    except Exception as e:
        print(f"Error in session maintenance: {e}")
        # Log the error properly
        current_app.logger.error(f"Session maintenance error: {e}")

# Initialize scheduler
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(scheduled_session_maintenance, 'interval', hours=6)
scheduler.start()

def init_admin(app):
    """Initialize admin extensions with the app"""
    bcrypt.init_app(app)
    csrf.init_app(app)

    try:
        if not scheduler.running:
            scheduler.start()
            app.logger.info("Admin scheduler started")
    except Exception as e:
        app.logger.error(f"Failed to start scheduler: {e}")

# Database Configuration
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_ADMIN_USER', 'root'),
    'port': int(os.getenv('DB_PORT', 3309)),
    'password': os.getenv('DB_ADMIN_PASSWORD', 'root'),
    'database': os.getenv('DB_NAME', 'ecommerce'),
    'buffered': True
}

# Constants
PRODUCT_IMAGE_DIR = os.path.join('static', 'img', 'products')
THUMBNAIL_DIR = os.path.join('static', 'img', 'thumbs')
DEFAULT_IMAGE = 'default.jpg'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webm', 'mp4'}
# Ensure upload directories exist
os.makedirs(PRODUCT_IMAGE_DIR, exist_ok=True)
os.makedirs(THUMBNAIL_DIR, exist_ok=True)

# Forms
class LoginForm(Form):
    username = StringField('Username', [validators.Length(min=1, max=50)])
    password = PasswordField('Password', [validators.Length(min=1)])

class ProductForm(FlaskForm):
    name = StringField('Product Name', [validators.DataRequired(), validators.Length(max=100)])
    description = TextAreaField('Description', validators=[validators.Optional()])
    product_features = TextAreaField('Product Features', validators=[validators.Optional()])
    care_instructions = TextAreaField('Care Instructions', validators=[validators.Optional()])
    meta_title = StringField('Meta Title', [validators.Optional(), validators.Length(max=255)])
    meta_keywords = StringField('Meta Keywords', [validators.Optional(), validators.Length(max=255)])
    meta_description = TextAreaField('Meta Description', validators=[validators.Optional()])
    unit_price = DecimalField('Unit Price', [validators.DataRequired(), validators.NumberRange(min=0)])
    #tax_value = DecimalField('Tax Value')
    mrp = DecimalField('MRP', [validators.NumberRange(min=0)])
    sku = StringField('SKU', [validators.Length(max=50)])
    sku_variant = StringField('Group SKU (Links Colors & Sizes)', [validators.Length(max=100)])
    hsn_code = StringField('HSN Code', [validators.Length(max=10)])
    size = SelectField('Size', choices=[], validators=[validators.Optional()])
    color = SelectField('Color', choices=[], validators=[validators.Optional()])
    item_height = DecimalField('Item Height (cm)', [validators.NumberRange(min=0)])
    item_width = DecimalField('Item Width (cm)', [validators.NumberRange(min=0)])
    item_length = DecimalField('Item Length (cm)', [validators.NumberRange(min=0)])
    item_weight = DecimalField('Item Weight (g)', [validators.NumberRange(min=0)])
    material_cost = DecimalField('Material Cost', [validators.NumberRange(min=0)])
    gst_rate = SelectField('GST Rate', choices=[], default='3')
    category = SelectField('Category', choices=[])
    material = SelectField('Material', choices=[])
    stock_quantity = IntegerField('Stock Quantity', [validators.NumberRange(min=0)])
    reorder_level = IntegerField('Reorder Level', [validators.NumberRange(min=0)])
    is_active = SelectField('Status', choices=[('1', 'Active'), ('0', 'Inactive')], coerce=str)
    apply_watermark = BooleanField('Apply Watermark', default=True)

    def __init__(self, *args, **kwargs):
        super(ProductForm, self).__init__(*args, **kwargs)
        from config_manager import get_config
        self.size.choices = get_config('SIZE_CHOICES', [])
        self.color.choices = get_config('COLOR_CHOICES', [])
        self.gst_rate.choices = get_config('GST_CHOICES', [])
        self.material.choices = get_config('MATERIAL_CHOICES', [])

    def validate(self, extra_validators=None):
        if not super(ProductForm, self).validate(extra_validators):
            return False
        if self.unit_price.data and self.unit_price.data <= 0:
            self.unit_price.errors = list(self.unit_price.errors) + ['Price must be positive']
            return False
        if self.stock_quantity.data is not None and self.stock_quantity.data < 0:
            self.stock_quantity.errors = list(self.stock_quantity.errors) + ['Stock cannot be negative']
            return False
        return True


class CompanyInfoForm(FlaskForm):
    company_name = StringField('Company Name', [validators.DataRequired(), validators.Length(max=100)])
    website = StringField('Website', [validators.Length(max=255)])
    address = TextAreaField('Address', [validators.DataRequired()])
    phone = StringField('Phone Number', [validators.Length(max=20)])
    email = StringField('Email', [validators.Email(), validators.Length(max=100)])
    gstin = StringField('GSTIN', [validators.Length(max=15)])
    pan = StringField('PAN', [validators.Length(max=10)])
    state = SelectField('State', choices=[], validate_choice=False)
    city = SelectField('City', choices=[], validate_choice=False)
    state_code = StringField('State Code', [validators.Length(max=2)])
    pincode = StringField('Pincode', [validators.Length(min=6, max=6)])

# Add this near your other form classes
class AdminUserForm(FlaskForm):
    username = StringField('Username', [
        validators.DataRequired(),
        validators.Length(min=4, max=50),
        validators.Regexp('^[a-zA-Z0-9_]+$', message="Username can only contain letters, numbers and underscores")
    ])
    email = StringField('Email', [
        validators.DataRequired(),
        validators.Email(),
        validators.Length(max=100)
    ])
    password = PasswordField('Password', [
        validators.Optional(),  # Only required for new users, handled in validate()
        validators.Length(min=8, message="Password must be at least 8 characters")
    ])
    confirm_password = PasswordField('Confirm Password', [
        validators.EqualTo('password', message='Passwords must match')
    ])
    role = SelectField('Role', choices=[], validators=[validators.DataRequired()])
    is_active = SelectField('Status', choices=[
        ('1', 'Active'),
        ('0', 'Inactive')
    ], coerce=str)
    mobile_number = StringField('Mobile Number', [
        validators.Optional(),
        validators.Length(max=15),
        validators.Regexp('^[0-9+]+$', message="Enter a valid phone number")
    ])
    expiry_date = DateField('Account Expiry Date', format='%Y-%m-%d', validators=[
        validators.Optional()
    ])

    def __init__(self, *args, **kwargs):
        super(AdminUserForm, self).__init__(*args, **kwargs)
        try:
            from extensions import db
            from models import AdminRoles
            roles = db.session.query(AdminRoles).filter_by(is_active=1).all()
            self.role.choices = [(r.name, r.name.capitalize()) for r in roles]
        except Exception as e:
            print(f"Error fetching roles: {e}")
            self.role.choices = [('admin', 'Admin')]

    def validate(self, extra_validators=None):
        if not super(AdminUserForm, self).validate(extra_validators):
            return False
        is_edit = getattr(self, 'is_edit', False)
        if not is_edit and not self.password.data:
            self.password.errors = list(self.password.errors) + ['Password is required']
            return False
        return True

class CategoryForm(FlaskForm):
    name = StringField('Category Name', [validators.DataRequired(), validators.Length(max=100)])
    slug = StringField('Slug', [validators.DataRequired(), validators.Length(max=100)])
    parent_id = SelectField('Parent Category', coerce=int, validators=[validators.Optional()])
    sort_order = IntegerField('Sort Order', default=0)
    is_active = SelectField('Status', choices=[('1', 'Active'), ('0', 'Inactive')], coerce=str)

# Helper for categories
def get_categories_for_choices():
    choices = [('', 'Select Category')]
    try:
        from extensions import db
        from models import Categories
        categories = db.session.scalars(db.select(Categories).filter_by(is_active=1)).all()
        
        cat_dict = {cat.id: cat for cat in categories}
        
        def get_full_path(cat):
            path = [cat.name]
            current = cat
            while current.parent_id and current.parent_id in cat_dict:
                current = cat_dict[current.parent_id]
                path.insert(0, current.name)
            return " > ".join(path)
            
        cat_with_paths = []
        for category in categories:
            cat_with_paths.append((str(category.id), get_full_path(category)))
            
        cat_with_paths.sort(key=lambda x: x[1])
        choices.extend(cat_with_paths)
    except Exception as e:
        print(f"Error fetching categories: {e}")
    return choices

from mysql.connector.pooling import MySQLConnectionPool

# Initialize connection pool with admin config
is_pythonanywhere = 'PYTHONANYWHERE_SITE' in os.environ
pool_size = 1 if is_pythonanywhere else 8

try:
    admin_db_pool = MySQLConnectionPool(
        pool_name="admin_ecom_pool",
        pool_size=pool_size,
        pool_reset_session=True,
        **db_config
    )
    print("Admin Database Connection Pool initialized successfully.")
except Exception as pool_err:
    print(f"Failed to initialize Admin Connection Pool: {pool_err}")
    admin_db_pool = None

# Database Connection
def get_db_connection():
    # Deprecated: Use extensions.db instead
    return None

@admin_bp.teardown_request
def close_admin_db_connection(exception=None):
    conns = g.pop('admin_db_conns', [])
    for db in conns:
        try:
            if db and hasattr(db, 'is_connected') and db.is_connected():
                db.close()
        except Exception as e:
            print(f"Error returning admin connection to pool: {e}")

@admin_bp.route('/')
def admin_index():
    return redirect(url_for('admin_bp.admin_login'))

# Helper Functions
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_unique_thumbnail_name(*args):
    import uuid
    if len(args) == 3:
        product_id, sku, ext = args
        ext = ext.lstrip('.').lower()
        return f"thumb_{product_id}_{sku}_{uuid.uuid4().hex[:8]}.{ext}"
    else:
        filename = args[0]
        ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
        return f"thumb_{uuid.uuid4().hex[:8]}.{ext}"

def create_global_thumbnail(image_path, thumbnail_name):
    try:
        from flask import current_app
        with Image.open(image_path) as img:
            img.thumbnail((300, 300))
            thumb_path = os.path.join(current_app.root_path, THUMBNAIL_DIR, thumbnail_name)
            img.save(thumb_path)
    except Exception as e:
        print(f"Thumbnail error: {e}")

def create_thumbnail(image_path, thumbnail_name):
    return create_global_thumbnail(image_path, thumbnail_name)

def add_watermark_to_image(image_path):
    try:
        from PIL import Image, ImageDraw, ImageFont
        with Image.open(image_path) as img:
            original_mode = img.mode
            img_rgba = img.convert('RGBA')
            
            watermark = Image.new('RGBA', img.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(watermark)
            
            text = "AANYAAS"
            font_size = 20
            
            try:
                # Scale font to approx 10% of image width
                font_size = max(int(img.width * 0.10), 20)
                font = ImageFont.truetype("arial.ttf", font_size)
            except IOError:
                font = ImageFont.load_default()
            
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                textwidth = bbox[2] - bbox[0]
                textheight = bbox[3] - bbox[1]
                offset_x = bbox[0]
                offset_y = bbox[1]
            except AttributeError:
                ts = getattr(draw, 'textsize', None)
                if ts:
                    textwidth, textheight = ts(text, font)
                else:
                    textwidth, textheight = 100, 20
                offset_x = 0
                offset_y = 0
            
            # Add padding to avoid clipping of descending/ascending chars during rotation
            padding = max(40, int(font_size * 0.5))
            
            # Create a separate image for the text to rotate it
            txt_img = Image.new('RGBA', (int(textwidth + padding*2), int(textheight + padding*2)), (255, 255, 255, 0))
            txt_draw = ImageDraw.Draw(txt_img)
            # Semi-transparent dark overlay for watermark, draw taking offsets and padding into account
            txt_draw.text((padding - offset_x, padding - offset_y), text, fill=(100, 100, 100, 100), font=font)
            
            # Rotate the text image by 30 degrees
            rotated_txt = txt_img.rotate(30, expand=1, fillcolor=(255, 255, 255, 0))
            
            # Paste the rotated text into the center of the watermark layer
            x = (img.width - rotated_txt.width) // 2
            y = (img.height - rotated_txt.height) // 2
            watermark.paste(rotated_txt, (x, y), mask=rotated_txt)
            
            # Composite the watermark over the original image
            watermarked_img = Image.alpha_composite(img_rgba, watermark)
            
            if original_mode != 'RGBA' and image_path.lower().endswith(('.jpg', '.jpeg')):
                watermarked_img = watermarked_img.convert('RGB')
                
            watermarked_img.save(image_path)
            return True
    except Exception as e:
        print(f"Failed to add watermark to {image_path}: {e}")
        return False

def to_locale_string(value):
    try:
        val = float(value)
        return "₹{:,.2f}".format(val)  # Adds ₹ symbol and formats with commas
    except (ValueError, TypeError):
        return str(value)

# Register the filter with the blueprint
admin_bp.add_app_template_filter(to_locale_string, name='toLocaleString')

def is_valid_ip(ip):
    """Enhanced IP address validation"""
    if not ip:
        return False

    # Handle IPv4 and IPv6
    ipv4_pattern = (
        r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
        r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
    )
    ipv6_pattern = (
        r'^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$|'
        r'^::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}$|'
        r'^[0-9a-fA-F]{1,4}::(?:[0-9a-fA-F]{1,4}:){0,5}'
        r'[0-9a-fA-F]{1,4}$|^[0-9a-fA-F]{1,4}:[0-9a-fA-F]{1,4}::'
        r'(?:[0-9a-fA-F]{1,4}:){0,4}[0-9a-fA-F]{1,4}$'
    )

    # Check for private/local IPs
    if ip in ('127.0.0.1', '::1', 'localhost', '0.0.0.0'):
        return False

    # Check for private network ranges
    if ip.startswith(('10.', '192.168.', '172.16.', '169.254.')):
        return False

    return bool(re.match(ipv4_pattern, ip) or bool(re.match(ipv6_pattern, ip)))

def get_geolocation(ip_address):
    """Enhanced geolocation with better fallbacks and caching"""
    if not ip_address or not is_valid_ip(ip_address):
        return {'country': 'Local', 'city': 'Local', 'region': 'Local'}

    # Try multiple services with timeout
    services = [
        {
            'url': f'https://ipapi.co/{ip_address}/json/',
            'keys': {'country': 'country_name', 'city': 'city', 'region': 'region'}
        },
        {
            'url': f'http://ip-api.com/json/{ip_address}',
            'keys': {'country': 'country', 'city': 'city', 'region': 'regionName'}
        },
        {
            'url': f'https://ipinfo.io/{ip_address}/json',
            'keys': {'country': 'country', 'city': 'city', 'region': 'region'}
        }
    ]

    for service in services:
        try:
            url = str(service['url'])
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                data = response.json()

                # Skip if service returned an error
                if data.get('error') or data.get('status') == 'fail':
                    continue

                # Extract data using service-specific keys
                s_keys = service['keys']
                if isinstance(s_keys, dict):
                    country = data.get(str(s_keys.get('country')), 'Unknown')
                    city = data.get(str(s_keys.get('city')), 'Unknown')
                    region = data.get(str(s_keys.get('region')), 'Unknown')
                else:
                    country, city, region = 'Unknown', 'Unknown', 'Unknown'

                # Validate we got real data
                if country and country != 'Unknown':
                    return {
                        'country': country,
                        'city': city if city and city != 'Unknown' else 'Unknown',
                        'region': region if region and region != 'Unknown' else 'Unknown'
                    }
        except (requests.RequestException, json.JSONDecodeError):
            continue

    return {'country': 'Unknown', 'city': 'Unknown', 'region': 'Unknown'}

def refresh_geolocation_data():
    """Refresh geolocation data for sessions with unknown location (standalone version)"""
    from extensions import db
    try:
        from sqlalchemy import text # type: ignore
        cursor_res = db.session.execute(text("""
            SELECT session_id as id, ip_address 
            FROM user_sessions 
            WHERE country IS NULL OR country = 'Unknown'
            LIMIT 100
        """)).fetchall()
        
        sessions_to_update = [dict(row._mapping) for row in cursor_res]
        
        updated_count = 0
        for session in sessions_to_update:
            if not session['ip_address'] or session['ip_address'] in ['127.0.0.1', 'localhost', '::1']:
                continue
                
            geo_data = get_geolocation(session['ip_address'])
            if geo_data['country'] != 'Unknown':
                db.session.execute(text("""
                    UPDATE user_sessions 
                    SET city = :city, region = :region, country = :country
                    WHERE session_id = :id
                """), {'city': geo_data['city'], 'region': geo_data['region'], 'country': geo_data['country'], 'id': session['id']})
                updated_count += 1
                
        if updated_count > 0:
            db.session.commit()
            print(f"Refreshed geolocation for {updated_count} sessions")
            
    except Exception as e:
        db.session.rollback()
        print(f"Error refreshing geolocation: {e}")

@admin_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("50 per minute")
def admin_login():
    if 'admin_logged_in' in session:
        return redirect(url_for('admin_bp.admin_dashboard'))
    
    form = LoginForm(request.form)
    if request.method == 'POST' and form.validate():
        try:
            from extensions import db
            from models import AdminUsers
            from datetime import datetime
            import bcrypt
            
            user = db.session.scalars(db.select(AdminUsers).filter_by(username=form.username.data, is_active='1')).first()
            password_data = form.password.data or ''
            if user and user.password and bcrypt.checkpw(password_data.encode('utf-8'), user.password.encode('utf-8')):
                session['admin_logged_in'] = True
                session['admin_username'] = user.username
                session['admin_role'] = user.role
                session['admin_id'] = user.id
                
                # Update last login
                user.last_login = datetime.now()
                db.session.commit()
                
                flash('Welcome back!', 'success')
                return redirect(request.args.get('next') or url_for('admin_bp.admin_dashboard'))
            else:
                flash('Invalid username or password', 'danger')
        except Exception as e:
            print(f"Login error: {e}")
            flash('System error during login', 'danger')
            
    return render_template('admin/login.html', form=form)

@admin_bp.route('/logout')
@admin_login_required
def admin_logout():
    # Session tracking middleware will handle setting logout_time
    session.get('admin_username', 'Unknown')
    session.clear()
    flash(f'Logged out successfully', 'success')
    return redirect(url_for('admin_bp.admin_login'))













@admin_bp.route('/get-last-login')
@admin_login_required
def get_last_login():
    try:
        from extensions import db
        from sqlalchemy import text # type: ignore
        admin_id = session.get('admin_user_id')
        if not admin_id:
            return jsonify({'success': False})
            
        res = db.session.execute(text("""
            SELECT last_login FROM users WHERE id = :id
        """), {'id': admin_id}).fetchone()
        
        if res and res.last_login:
            from datetime import datetime
            if isinstance(res.last_login, datetime):
                # Format relative time
                now = datetime.now()
                diff = now - res.last_login
                
                if diff.days == 0:
                    if diff.seconds < 3600:
                        mins = diff.seconds // 60
                        time_str = f"{mins} mins ago" if mins > 0 else "Just now"
                    else:
                        hours = diff.seconds // 3600
                        time_str = f"{hours} hours ago"
                elif diff.days == 1:
                    time_str = "Yesterday"
                else:
                    time_str = f"{diff.days} days ago"
                    
                return jsonify({'success': True, 'last_login': time_str})
                
        return jsonify({'success': False})
    except Exception as e:
        print(f"Error getting last login: {e}")
        return jsonify({'success': False})






























# ===== TESTIMONIALS MANAGEMENT =====



# Category Management Routes





import json



# Import modularized routes
import admin.routes.inventory
import admin.routes.orders
import admin.routes.reports
import admin.routes.settings
import admin.routes.users
import admin.routes.gift_cards
import admin.routes.marketing
import admin.routes.dashboard
import admin.routes.roles_menus
