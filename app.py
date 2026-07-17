import os
os.environ['TZ'] = 'Asia/Kolkata'

from datetime import datetime, timedelta, UTC
from flask_mail import Mail
from dotenv import load_dotenv
from config_manager import get_config
from flask import Flask, session, jsonify, request, render_template
from flask_wtf.csrf import CSRFProtect, generate_csrf, CSRFError
from apscheduler.schedulers.background import BackgroundScheduler

# PhonePe SDK Imports
try:
    from phonepe.sdk.pg.payments.v2.standard_checkout_client import StandardCheckoutClient
    from phonepe.sdk.pg.env import Env
    PHONEPE_AVAILABLE = True
except ImportError:
    PHONEPE_AVAILABLE = False
    print("Warning: phonepe_sdk not installed properly.")
from utils.order_helpers import finalize_successful_order, cancel_failed_order

load_dotenv()

from utils.logger import setup_logger
from flask_session import Session

app = Flask(__name__, template_folder='templates', static_folder='static')
setup_logger(app)

import urllib.parse

db_user = os.getenv('DB_USER', 'root')
db_password = urllib.parse.quote_plus(os.getenv('DB_PASSWORD', ''))
db_host = os.getenv('DB_HOST', 'localhost')
db_port = os.getenv('DB_PORT', '3309')
db_name = os.getenv('DB_NAME', '')

app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+mysqlconnector://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
# Render.com PostgreSQL override
_render_db_url = os.environ.get('DATABASE_URL', '')
if _render_db_url:
    if _render_db_url.startswith('postgres://'):
        _render_db_url = 'postgresql://' + _render_db_url[11:]
    app.config['SQLALCHEMY_DATABASE_URI'] = _render_db_url
    print('Using PostgreSQL from DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Detect PythonAnywhere environment
is_pythonanywhere = 'PYTHONANYWHERE_SITE' in os.environ

if is_pythonanywhere:
    # PythonAnywhere free accounts have a strict limit of 6 max concurrent connections.
    # Keep pool size very small to prevent resource exhaustion.
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 2,
        'max_overflow': 0,
        'pool_recycle': 280,
        'pool_pre_ping': True
    }
else:
    # Enterprise Connection Pooling for local/VPS environment
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 20,
        'max_overflow': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True
    }

from extensions import db
db.init_app(app)

# Register database connection pool teardown handler
from database import close_db_connection
app.teardown_appcontext(close_db_connection)


# Email configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')
app.config['MAIL_DEBUG'] = False
mail = Mail(app)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'aanyaas-dev-key-2026')

# Server-Side Session Configuration
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
Session(app)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

# Global configuration
@app.template_filter('color_name')
def color_name_filter(s):
    if not s:
        return ''
    color_map = get_config('COLOR_NAME_MAP', {}) or {}
    return color_map.get(s.lower(), s.title())

# Constants
app.config['DEFAULT_IMAGE'] = 'default.jpg'
app.config['THUMBNAIL_DIR'] = os.path.join(app.root_path, 'static', 'img', 'thumbs')
app.config['PRODUCT_IMAGE_DIR'] = os.path.join(app.root_path, 'static', 'img', 'products')
app.config['PROFILE_IMAGE_DIR'] = os.path.join(app.root_path, 'static', 'img', 'profiles')
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

# Instagram Configuration
app.config['INSTAGRAM_ACCESS_TOKEN'] = os.getenv('INSTAGRAM_ACCESS_TOKEN')

# Maximum allowed file upload size: 16 MB
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# app.permanent_session = True # Removed: Invalid Flask attribute

# Production Security Settings
if os.getenv('PHONEPE_ENV') == 'PRODUCTION':
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
    )
    # Ensure redirect_url uses https even if behind a proxy (PythonAnywhere uses a proxy)
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)

csrf = CSRFProtect()
csrf.init_app(app)

# Shared rate limiter — used by app and blueprints via utils/limiter_shared.py
from utils.limiter_shared import limiter
limiter.init_app(app)

# Shared response cache — used by blueprints via utils/cache_shared.py
from utils.cache_shared import cache
cache.init_app(app)

# Initialize PhonePe Client
phonepe_client = None
if PHONEPE_AVAILABLE:
    try:
        pe_env = Env.PRODUCTION if os.getenv('PHONEPE_ENV') == 'PRODUCTION' else Env.SANDBOX # type: ignore
        phonepe_client = StandardCheckoutClient.get_instance( # type: ignore
            client_id=os.getenv('PHONEPE_CLIENT_ID', 'your_client_id_here'),
            client_secret=os.getenv('PHONEPE_CLIENT_SECRET', 'your_client_secret_here'),
            client_version=int(os.getenv('PHONEPE_CLIENT_VERSION', 1)),
            env=pe_env,
            should_publish_events=False
        )
        print(f"PhonePe SDK initialized in {pe_env} mode.")
        app.phonepe_client = phonepe_client # type: ignore
    except Exception as e:
        print(f"Error initializing PhonePe Client: {str(e)}")

@app.before_request
def capture_referral():
    ref_code = request.args.get('ref')
    if ref_code:
        # Save referral code in session if not already logged in
        if 'user_id' not in session:
            session['referral_code'] = ref_code.strip()

@app.after_request
def apply_secure_headers(response):
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: https:;"
    return response


def reconcile_phonepe_payments():
    """Background task to reconcile pending PhonePe payments and refunds (mandatory UAT checklist)."""
    if not phonepe_client:
        return

    try:
        with app.app_context():
            from models import Orders
            from extensions import db
            from sqlalchemy import text  # type: ignore
            from datetime import datetime, timedelta, UTC

            time_threshold = datetime.now() - timedelta(hours=24)
            
            # 1. Reconcile Pending Payments
            pending_orders = db.session.execute(
                db.select(Orders.id).where(
                    Orders.payment_method == 'online',
                    Orders.status == 'pending',
                    Orders.order_date > time_threshold
                )
            ).scalars().all()

            for order_id in pending_orders:
                merchant_order_id = f"OR_{order_id}"
                try:
                    status_res = phonepe_client.get_order_status(merchant_order_id, details=False) # type: ignore
                    if status_res.state == 'COMPLETED':
                        finalize_successful_order(order_id, merchant_order_id)
                        print(f"Reconciliation: Order {order_id} marked as COMPLETED")
                    elif status_res.state == 'FAILED':
                        cancel_failed_order(order_id, "Reconciliation checked: FAILED")
                        print(f"Reconciliation: Order {order_id} marked as FAILED")
                except Exception as pe_err:
                    error_msg = str(pe_err)
                    print(f"Status check failed for {merchant_order_id}: {error_msg}")
                    if "ORDER_NOT_FOUND" in error_msg:
                        try:
                            cancel_failed_order(order_id, "Reconciliation checked: ORDER_NOT_FOUND")
                            print(f"Reconciliation: Order {order_id} marked as CANCELLED (Not found in PhonePe)")
                        except Exception as db_err:
                            print(f"Database error marking order {order_id} cancelled: {str(db_err)}")

            # 2. Reconcile Ongoing Refunds
            ongoing_refunds = db.session.execute(
                db.select(Orders.id, Orders.merchant_refund_id).where(
                    Orders.refund_status.in_(['PENDING', 'CONFIRMED']),
                    Orders.merchant_refund_id != None
                )
            ).all()

            for refund in ongoing_refunds:
                order_id = refund.id
                merchant_refund_id = refund.merchant_refund_id
                try:
                    refund_status_res = phonepe_client.get_refund_status(merchant_refund_id) # type: ignore
                    if refund_status_res.state == 'COMPLETED':
                        db.session.execute(
                            db.update(Orders).where(Orders.id == order_id).values(refund_status='COMPLETED')
                        )
                        db.session.commit()
                        print(f"Reconciliation: Refund for Order {order_id} marked as COMPLETED")
                    elif refund_status_res.state == 'FAILED':
                        db.session.execute(
                            db.update(Orders).where(Orders.id == order_id).values(refund_status='FAILED')
                        )
                        db.session.commit()
                        print(f"Reconciliation: Refund for Order {order_id} marked as FAILED")
                except Exception as pe_err:
                    error_msg = str(pe_err)
                    print(f"Refund status check failed for {merchant_refund_id}: {error_msg}")
                    if "ORDER_NOT_FOUND" in error_msg or "REFUND_NOT_FOUND" in error_msg:
                        try:
                            db.session.execute(
                                db.update(Orders).where(Orders.id == order_id).values(refund_status='FAILED')
                            )
                            db.session.commit()
                            print(f"Reconciliation: Refund for Order {order_id} marked as FAILED (Not found)")
                        except Exception as db_err:
                            print(f"Database error marking refund {order_id} failed: {str(db_err)}")

    except Exception as e:
        print(f"Reconciliation Error: {str(e)}")
    finally:
        try:
            from extensions import db
            db.session.remove()
        except Exception:
            pass


from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

# Share connection string and quotes to prevent special character parsing bugs
db_url = app.config['SQLALCHEMY_DATABASE_URI']

jobstore_engine_options = {}
if is_pythonanywhere:
    jobstore_engine_options = {
        'pool_size': 1,
        'max_overflow': 0,
        'pool_recycle': 280
    }

jobstores = {
    'default': SQLAlchemyJobStore(url=db_url, engine_options=jobstore_engine_options)
}

# --- AFTER ---
# Start persistent background scheduler (Only on the active Flask worker process)
if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
    scheduler = BackgroundScheduler(jobstores=jobstores, daemon=True)
    scheduler.add_job(reconcile_phonepe_payments, 'interval', seconds=30, id='reconcile_phonepe_payments', replace_existing=True)

    from utils.abandoned_cart import process_abandoned_carts
    scheduler.add_job(process_abandoned_carts, 'interval', hours=1, id='process_abandoned_carts', replace_existing=True)

    from utils.db_backup import perform_database_backup
    scheduler.add_job(perform_database_backup, 'cron', hour=2, minute=0, id='perform_database_backup', replace_existing=True)

    scheduler.start()
    print("Background scheduler started successfully.")


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return render_template('admin/csrf_error.html', reason=e.description), 400

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500


# ---------------------------------------------------------------------------
# Register all blueprints
# ---------------------------------------------------------------------------
from admin.admin_app import admin_bp
from blueprints.auth import auth_bp
from blueprints.shop import shop_bp
from blueprints.cart import cart_bp
from blueprints.user import user_bp
from blueprints.pages import pages_bp
from blueprints.checkout import checkout_bp
from blueprints.pos import pos_bp
from blueprints.chatbot import chatbot_bp

app.register_blueprint(admin_bp, url_prefix="/admin")
app.register_blueprint(auth_bp)
app.register_blueprint(shop_bp)
app.register_blueprint(cart_bp)
app.register_blueprint(user_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(checkout_bp)
app.register_blueprint(pos_bp)
app.register_blueprint(chatbot_bp)

# ---------------------------------------------------------------------------
# Exempt PhonePe webhook routes from CSRF (registered in checkout blueprint)
# ---------------------------------------------------------------------------
csrf.exempt(app.view_functions.get('checkout_bp.phonepe_webhook'))
csrf.exempt(app.view_functions.get('checkout_bp.phonepe_callback'))
csrf.exempt(app.view_functions.get('pos_bp.phonepe_redirect'))

app.jinja_env.globals.update(csrf_token=generate_csrf, get_config=get_config)  # type: ignore[name-defined]

from utils.session_helpers import (
    get_guest_or_user_cart_count,
    get_nav_categories,
    get_wishlist_product_ids,
    get_company_info
)


@app.context_processor
def inject_global_data():
    company = get_company_info()
    return dict(
        cart_count=get_guest_or_user_cart_count(),
        nav_categories=get_nav_categories(),
        wishlist_product_ids=get_wishlist_product_ids(),
        company_info=company,
        company_name=company.company_name if company else 'Aanyaas Enterprises',
        brand_name=get_config('BRAND_NAME', 'Aanyaas')
    )


@app.before_request
def make_session_permanent():
    session.permanent = True

from utils.session_tracking import track_user_session

@app.before_request
def record_user_session():
    # Skip tracking static files to save DB writes
    if request.path.startswith('/static/') or request.path == '/favicon.ico':
        return
    track_user_session()


@app.route('/health')
def health_check():
    try:
        from sqlalchemy import text  # type: ignore
        db.session.execute(text("SELECT 1"))
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'timestamp': datetime.now(UTC).isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'database': 'disconnected',
            'error': str(e),
            'timestamp': datetime.now(UTC).isoformat()
        }), 500


# ---------------------------------------------------------------------------
# For local development server
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)
