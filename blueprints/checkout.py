import logging
import os
import json
import uuid
import traceback
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.utils import secure_filename

from flask import Blueprint, current_app, render_template, request, session, redirect, url_for, jsonify, flash, send_file


from config_manager import get_config
from utils.limiter_shared import limiter
from invoice_generator import generate_invoice_pdf
from utils.notifications import trigger_all_order_notifications
from utils.order_helpers import finalize_successful_order, cancel_failed_order

# PhonePe SDK Imports
try:
    from phonepe.sdk.pg.payments.v2.models.request.standard_checkout_pay_request import StandardCheckoutPayRequest
    from phonepe.sdk.pg.payments.v2.models.request.create_sdk_order_request import CreateSdkOrderRequest
    from phonepe.sdk.pg.common.models.request.meta_info import MetaInfo
    from phonepe.sdk.pg.common.exceptions import PhonePeException
    from phonepe.sdk.pg.payments.v2.models.request.prefill_user_login_details import PrefillUserLoginDetails
    PHONEPE_AVAILABLE = True
except ImportError:
    PHONEPE_AVAILABLE = False

from sqlalchemy import text, func, or_, and_  # type: ignore
from extensions import db
from models import Orders, OrderItems, Products, OrderReturns, ReturnItems, Users, ProductReviews

checkout_bp = Blueprint('checkout_bp', __name__)

def csrf_exempt(f):
    f._csrf_exempt = True
    return f

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth_bp.login', next=request.url))
        return f(*args, **kwargs)
    return decorated

def utc_to_ist(dt):
    """
    Return the datetime object. Since the server system time is already
    set to Asia/Kolkata (IST), manual offset addition is not required.
    """
    if dt is None:
        return None
    return dt

def process_products(items):
    processed = []
    for item in items:
        d = dict(item)
        price = d.get('price')
        if price is None:
            price = 0
        if 'unit_price' not in d:
            d['unit_price'] = float(price)

        quantity = d.get('quantity')
        if quantity is None:
            quantity = 1

        d['item_total'] = float(d['unit_price']) * int(quantity)
        processed.append(d)
    return processed

def check_if_invoice_available(order_id):
    invoice_path = os.path.join(str(current_app.static_folder), 'invoices', f'invoice_{order_id}.pdf')
    return os.path.exists(invoice_path)

def calculate_gst_breakdown(order_id):
    """Calculate GST breakdown for an order (item-wise and total)"""
    try:
        from models import CompanyInfo, Orders, Users, OrderItems, Products
        import json
        from decimal import Decimal, getcontext
        getcontext().prec = 28
        
        company = db.session.scalars(db.select(CompanyInfo).limit(1)).first()
        price_includes_gst = company.price_includes_gst if company else True
        if not company or not company.gstin or not company.state_code:
            raise Exception("Company GST details not configured")

        order = db.session.scalars(db.select(Orders).filter_by(id=order_id)).first()
        if not order:
            raise Exception("Order not found")
        
        user = db.session.scalars(db.select(Users).filter_by(id=order.user_id)).first()
        
        billing_data = {}
        try:
            if order.billing_address:
                billing_data = json.loads(order.billing_address)
        except:
            pass
            
        customer_state_code = billing_data.get('state_code', '')
        company_state = str(company.state_code or '').strip().strip('"\'')
        customer_state = str(customer_state_code).strip().strip('"\'')
        is_intra_state = (company_state == customer_state)
        
        # If historical GST columns are populated, return them directly to prevent recalculation drift!
        if order.taxable_amount is not None and float(order.taxable_amount) > 0:
            items = db.session.scalars(db.select(OrderItems).filter_by(order_id=order_id)).all()
            
            customer_gstin = billing_data.get('gst_number') or billing_data.get('company_name') or (user.gstin if user else '')
            
            gst_breakdown = {
                'items': [],
                'total_item_quantity': Decimal(0),
                'total_price': Decimal(0),
                'total_item_discount': Decimal(str(order.discount_amount or 0)),
                'total_taxable_value': Decimal(str(order.taxable_amount or 0)),
                'total_cgst': Decimal(str(order.cgst_amount or 0)),
                'total_sgst': Decimal(str(order.sgst_amount or 0)),
                'total_igst': Decimal(str(order.igst_amount or 0)),
                'total_gst': Decimal(str(order.total_gst or 0)),
                'is_intra_state': is_intra_state,
                'company_gstin': company.gstin or '',
                'customer_gstin': customer_gstin,
                'company_state_code': company.state_code or '',
                'customer_state_code': customer_state_code
            }
            
            subtotal = Decimal(str(order.subtotal or 0))
            discount_percentage = Decimal(0)
            if gst_breakdown['total_item_discount'] > 0 and subtotal > 0:
                discount_percentage = (gst_breakdown['total_item_discount'] / subtotal) * Decimal(100)
            
            for item in items:
                product = db.session.scalars(db.select(Products).filter_by(id=item.product_id)).first()
                item_qty = Decimal(str(item.quantity or 1))
                item_price = Decimal(str(item.price or 0))
                gst_rate = Decimal(str(item.gst_rate or 18)) if item.gst_rate is not None else Decimal('18.00')
                taxable_value = Decimal(str(item.taxable_value or 0)) if item.taxable_value is not None else Decimal('0.00')
                cgst = Decimal(str(item.cgst_amount or 0)) if item.cgst_amount is not None else Decimal('0.00')
                sgst = Decimal(str(item.sgst_amount or 0)) if item.sgst_amount is not None else Decimal('0.00')
                igst = Decimal(str(item.igst_amount or 0)) if item.igst_amount is not None else Decimal('0.00')
                item_discount = (item_price * item_qty) * (discount_percentage / Decimal(100))
                
                gst_breakdown['items'].append({
                    'product_id': item.product_id,
                    'product_name': item.product_name,
                    'product_image': product.image if product else 'default.jpg',
                    'hsn_code': item.hsn_code,
                    'quantity': item_qty,
                    'unit_price': item_price,
                    'item_discount': item_discount,
                    'taxable_value': taxable_value,
                    'gst_rate': gst_rate,
                    'cgst': cgst,
                    'sgst': sgst,
                    'igst': igst,
                    'total_value': taxable_value + cgst + sgst + igst
                })
                gst_breakdown['total_item_quantity'] += item_qty
                gst_breakdown['total_price'] += item_price * item_qty
                
            return gst_breakdown

        items = db.session.scalars(db.select(OrderItems).filter_by(order_id=order_id)).all()

        discount_percentage = Decimal(0)
        if order.discount_amount and order.discount_amount > 0 and order.subtotal and order.subtotal > 0:
            discount_percentage = (Decimal(str(order.discount_amount)) / Decimal(str(order.subtotal))) * Decimal(100)

        customer_gstin = billing_data.get('gst_number') or billing_data.get('company_name') or (user.gstin if user else '')
        
        gst_breakdown = {
            'items': [],
            'total_item_quantity': Decimal(0),
            'total_price': Decimal(0),
            'total_item_discount': Decimal(0),
            'total_taxable_value': Decimal(0),
            'total_cgst': Decimal(0),
            'total_sgst': Decimal(0),
            'total_igst': Decimal(0),
            'total_gst': Decimal(0),
            'is_intra_state': is_intra_state,
            'company_gstin': company.gstin or '',
            'customer_gstin': customer_gstin,
            'company_state_code': company.state_code or '',
            'customer_state_code': customer_state_code
        }

        for item in items:
            product = db.session.scalars(db.select(Products).filter_by(id=item.product_id)).first()
            item_unit_price = Decimal(str(item.price or 0))
            item_quantity = Decimal(str(item.quantity or 1))
            gst_rate = Decimal(str((product.gst_rate if product else None) or 18)) if product and product.gst_rate is not None else Decimal('18.00')
            item_discount = (item_unit_price * item_quantity) * (discount_percentage / Decimal(100))
            if price_includes_gst:
                taxable_value = (item_unit_price * item_quantity - item_discount) / (Decimal('1') + gst_rate / Decimal('100'))
            else:
                taxable_value = item_unit_price * item_quantity - item_discount

            if is_intra_state:
                cgst = (taxable_value * (gst_rate / Decimal(2))) / Decimal(100)
                sgst = (taxable_value * (gst_rate / Decimal(2))) / Decimal(100)
                igst = Decimal(0)
            else:
                cgst = sgst = Decimal(0)
                igst = (taxable_value * gst_rate) / Decimal(100)

            gst_breakdown['items'].append({
                'product_id': item.product_id,
                'product_name': item.product_name,
                'product_image': product.image if product else 'default.jpg',
                'hsn_code': product.hsn_code if product else item.hsn_code,
                'quantity': item_quantity,
                'unit_price': item_unit_price,
                'item_discount': item_discount,
                'taxable_value': taxable_value,
                'gst_rate': gst_rate,
                'cgst': cgst,
                'sgst': sgst,
                'igst': igst,
                'total_value': taxable_value + cgst + sgst + igst
            })

            gst_breakdown['total_item_quantity'] += item_quantity
            gst_breakdown['total_price'] += item_unit_price * item_quantity
            gst_breakdown['total_item_discount'] += item_discount
            gst_breakdown['total_taxable_value'] += taxable_value
            gst_breakdown['total_cgst'] += cgst
            gst_breakdown['total_sgst'] += sgst
            gst_breakdown['total_igst'] += igst
            gst_breakdown['total_gst'] += (cgst + sgst + igst)

        return gst_breakdown
    except Exception as e:
        logging.info(f"Error in calculate_gst_breakdown: {str(e)}")
        return {'items': [], 'total_item_quantity': 0, 'total_price': 0, 'total_item_discount': 0, 'total_taxable_value': 0, 'total_cgst': 0, 'total_sgst': 0, 'total_igst': 0, 'total_gst': 0}

def send_order_confirmation_email(order_id):
    """Send order confirmation email to the customer"""
    try:
        from models import Orders, Users
        import json
        gst_data = calculate_gst_breakdown(order_id)
        if not gst_data or not gst_data['items']:
            return False

        order = db.session.scalars(db.select(Orders).filter_by(id=order_id)).first()
        if not order:
            return False
            
        user = db.session.scalars(db.select(Users).filter_by(id=order.user_id)).first()

        billing_address = json.loads(order.billing_address) if order.billing_address else {}
        customer_email = (user.email if user else None) or billing_address.get('email')
        if not customer_email:
            return False

        # Convert order to dict for template since template expects dictionary access
        order_dict = {
            'id': order.id,
            'order_date': order.order_date,
            'total_amount': order.total_amount,
            'subtotal': order.subtotal,
            'discount_amount': order.discount_amount,
            'shipping_charge': order.shipping_charge,
            'payment_method': order.payment_method,
            'status': order.status,
            'shipping_address': order.shipping_address,
            'billing_address': order.billing_address,
            'user_email': user.email if user else '' if user else None,
            'username': user.username if user else None
        }

        # Render email template
        html = render_template('emails/order_confirmation.html',
                              order=order_dict,
                              gst_breakdown=gst_data,
                              billing_address=billing_address)

        mail = current_app.extensions.get('mail')
        if mail:
            from flask_mail import Message
            msg = Message(
                f"Your Order Confirmed #{order.id} | Aanyaas Enterprises",
                recipients=[customer_email],
                html=html
            )
            mail.send(msg)
            return True
        return False
    except Exception as e:
        logging.info(f"Error sending order confirmation email: {str(e)}")
        traceback.print_exc()
        return False

@checkout_bp.route('/orders')
@login_required
def order_history():
    try:
        status_filter = request.args.get('status', 'all')
        search_query = request.args.get('q', '').strip()
        period_filter = request.args.get('period', 'last_month')
        user_id = session['user_id']

        # Build base query for Orders
        query = db.select(Orders).filter_by(user_id=user_id)

        # Apply period filter
        if period_filter != 'all_time':
            from datetime import timedelta, datetime
            now = datetime.now()
            if period_filter == 'last_month':
                query = query.filter(Orders.order_date >= now - timedelta(days=30))
            elif period_filter == 'last_3_months':
                query = query.filter(Orders.order_date >= now - timedelta(days=90))
            elif period_filter == 'this_year':
                query = query.filter(func.year(Orders.order_date) == now.year)
            elif period_filter == 'last_year':
                query = query.filter(func.year(Orders.order_date) == now.year - 1)

        # Apply status filter
        if status_filter != 'all':
            query = query.filter(Orders.status == status_filter)

        # Apply search query
        if search_query:
            query = query.filter(or_(
                Orders.id.cast(db.String).ilike(f"%{search_query}%"),
                Orders.order_items.any(OrderItems.product_name.ilike(f"%{search_query}%"))
            ))

        # Order by date
        query = query.order_by(Orders.order_date.desc())
        
        db_orders = db.session.scalars(query).all()
        
        orders = []
        for o in db_orders:
            # We need to assemble the data exactly like the template expects.
            # Eager load the order items and returns could have been done, but since the query
            # fetches order objects, we can access their relationships.
            # Note: accessing relationships here might cause N+1 problem, but we can do it efficiently
            
            # Fetch all items for this order with product details
            items = db.session.scalars(db.select(OrderItems).filter_by(order_id=o.id)).all()
            
            product_names = []
            product_images = []
            product_ids = []
            product_quantities = []
            returned_quantities = []
            reviewed_statuses = []
            
            item_count = len(items)
            sample_product_id = min((item.product_id for item in items), default=None)
            
            # Find the latest return for this order
            latest_return = db.session.scalars(
                db.select(OrderReturns).filter_by(order_id=o.id).order_by(OrderReturns.id.desc())
            ).first()
            
            # Calculate return quantities per product
            return_qtys_by_product = {}
            if latest_return:
                ret_items = db.session.scalars(db.select(ReturnItems).filter_by(return_id=latest_return.id)).all()
                for ri in ret_items:
                    return_qtys_by_product[ri.product_id] = return_qtys_by_product.get(ri.product_id, 0) + ri.quantity
            
            for item in items:
                product = db.session.scalars(db.select(Products).filter_by(id=item.product_id)).first()
                if product:
                    product_names.append(product.name)
                    product_images.append(product.image or '')
                    product_ids.append(str(product.id))
                    product_quantities.append(str(item.quantity))
                    returned_quantities.append(str(return_qtys_by_product.get(product.id, 0)))
                    
                    # Check if reviewed
                    is_reviewed = db.session.scalars(
                        db.select(ProductReviews).filter_by(user_id=user_id, product_id=product.id, order_id=o.id)
                    ).first()
                    reviewed_statuses.append('1' if is_reviewed else '0')
            
            order_dict = {
                'id': o.id,
                'order_date': o.order_date,
                'total_amount': o.total_amount,
                'status': o.status,
                'item_count': item_count,
                'sample_product_id': sample_product_id,
                'product_names_list': product_names,
                'product_images_list': product_images,
                'product_ids_list': product_ids,
                'product_quantities_list': product_quantities,
                'returned_quantities_list': returned_quantities,
                'reviewed_statuses_list': reviewed_statuses,
                'return_status': latest_return.status.value if latest_return and latest_return.status else None,
                'return_remarks': latest_return.remarks if latest_return else None,
                'return_amount': o.return_amount,
                'returned_at': o.returned_at,
                'has_invoice': check_if_invoice_available(o.id)
            }
            orders.append(order_dict)

        return render_template('order_history.html',
                               orders=orders,
                               status_filter=status_filter,
                               search_query=search_query,
                               period_filter=period_filter,
                               user_logged_in=True,
                               username=session.get("username"))
    except Exception as e:
        logging.info(f"Error fetching order history: {str(e)}")
        flash('Error loading order history. Please try again.', 'error')
        return render_template('order_history.html', orders=[], status_filter='all', search_query='', period_filter='last_month', user_logged_in=True, username=session.get('username'))

@checkout_bp.route('/checkout', methods=['GET', 'POST'])
@login_required
@limiter.limit("10 per minute")
def checkout():
    if request.method == 'POST':
        logging.info(f"Checkout POST received from user {session.get('user_id')}")
        try:
            from models import Cart, Products, UserAddresses, Users, Coupons, LoyaltyLedger, GiftCards, GiftCardTransactions, InventoryLogs, CompanyInfo, Orders, OrderItems
            from decimal import Decimal

            same_as_shipping = request.form.get('same_as_shipping')
            required_fields = {'shipping_address_id': 'Shipping address is required', 'payment_method': 'Payment method is required'}
            errors = {f: msg for f, msg in required_fields.items() if not request.form.get(f)}
            if errors:
                logging.info(f"Checkout Error: Missing fields {list(errors.keys())} for user {session.get('user_id')}")
                return jsonify({'success': False, 'message': 'Please select an address and payment method', 'errors': errors}), 400
            
            cart_query = db.session.execute(
                db.select(Cart, Products).join(Products, Cart.product_id == Products.id).filter(Cart.user_id == session['user_id'])
            ).all()
            
            cart_items = []
            for cart_item, product in cart_query:
                cart_items.append({
                    'id': product.id,
                    'name': product.name,
                    'price': float(product.price or 0),
                    'gst_rate': float(product.gst_rate or 18),
                    'hsn_code': product.hsn_code,
                    'image': product.image,
                    'quantity': cart_item.quantity,
                    'stock_quantity': product.stock_quantity,
                    'product_obj': product
                })
            
            cart_items = process_products(cart_items)
            if not cart_items:
                logging.info(f"Checkout Error: Empty cart for user {session.get('user_id')}")
                return jsonify({'success': False, 'message': 'Your cart is empty', 'redirect': url_for('cart_bp.cart')}), 400
                
            for item in cart_items:
                if item['quantity'] > item['stock_quantity']:
                    raise Exception(f"Insufficient stock for {item['name']}. Available: {item['stock_quantity']}")

            shipping_address_id = request.form.get('shipping_address_id')
            if shipping_address_id == 'new':
                required_shipping_fields = [
                    'shipping_first_name', 'shipping_email',
                    'shipping_phone', 'shipping_address1', 'shipping_city',
                    'shipping_state', 'shipping_zip_code'
                ]
                missing = [f for f in required_shipping_fields if not request.form.get(f)]
                if missing:
                    return jsonify({
                        'success': False,
                        'message': 'Missing required fields for new shipping address',
                        'errors': {f: 'This field is required' for f in missing}
                    }), 400

                shipping_address = {
                    'first_name': request.form.get('shipping_first_name', ''),
                    'last_name': request.form.get('shipping_last_name', ''),
                    'email': request.form.get('shipping_email', ''),
                    'phone': request.form.get('shipping_phone', ''),
                    'address1': request.form.get('shipping_address1', ''),
                    'address2': request.form.get('shipping_address2', ''),
                    'city': request.form.get('shipping_city', ''),
                    'state': request.form.get('shipping_state', ''),
                    'zip_code': request.form.get('shipping_zip_code', ''),
                    'state_code': request.form.get('shipping_state_code', ''),
                    'company_name': request.form.get('shipping_company_name', ''),
                    'gst_number': request.form.get('shipping_gst_number', ''),
                    'address_type': request.form.get('address_type', 'Home')
                }
            else:
                saved_shipping = db.session.scalars(db.select(UserAddresses).filter_by(id=shipping_address_id, user_id=session['user_id'])).first()
                if not saved_shipping:
                    return jsonify({
                        'success': False,
                        'message': 'Invalid shipping address selected',
                        'errors': {'shipping_address_id': 'Address not found or does not belong to you'}
                    }), 400

                user = db.session.scalars(db.select(Users).filter_by(id=session['user_id'])).first()
                user_email = user.email if user else ''

                full_name = saved_shipping.full_name or ''
                name_parts = full_name.split(' ') if full_name else ['', '']
                first_name = name_parts[0] if name_parts else ''
                last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

                shipping_address = {
                    'first_name': first_name,
                    'last_name': last_name,
                    'email': saved_shipping.email or user_email,
                    'phone': saved_shipping.mobile_number,
                    'address1': saved_shipping.address_line1,
                    'address2': saved_shipping.address_line2 or '',
                    'city': saved_shipping.city,
                    'state': saved_shipping.state,
                    'zip_code': saved_shipping.postal_code,
                    'state_code': saved_shipping.state_code or '',
                    'company_name': saved_shipping.company_name or '',
                    'gst_number': saved_shipping.gst_number or '',
                    'address_type': saved_shipping.address_type or 'Home'
                }

            if same_as_shipping:
                billing_address = shipping_address.copy()
            else:
                billing_address_id = request.form.get('billing_address_id')
                if billing_address_id == 'new':
                    required_billing_fields = [
                        'billing_first_name', 'billing_email',
                        'billing_phone', 'billing_address1', 'billing_city',
                        'billing_state', 'billing_zip_code'
                    ]
                    missing = [f for f in required_billing_fields if not request.form.get(f)]
                    if missing:
                        return jsonify({
                            'success': False,
                            'message': 'Missing required fields for new billing address',
                            'errors': {f: 'This field is required' for f in missing}
                        }), 400
                    billing_address = {
                        'first_name': request.form.get('billing_first_name', ''),
                        'last_name': request.form.get('billing_last_name', ''),
                        'email': request.form.get('billing_email', ''),
                        'phone': request.form.get('billing_phone', ''),
                        'address1': request.form.get('billing_address1', ''),
                        'address2': request.form.get('billing_address2', ''),
                        'city': request.form.get('billing_city', ''),
                        'state': request.form.get('billing_state', ''),
                        'zip_code': request.form.get('billing_zip_code', ''),
                        'state_code': request.form.get('billing_state_code', ''),
                        'company_name': request.form.get('billing_company_name', ''),
                        'gst_number': request.form.get('billing_gst_number', ''),
                        'address_type': request.form.get('address_type', 'Home')
                    }
                else:
                    saved_address = db.session.scalars(db.select(UserAddresses).filter_by(id=billing_address_id, user_id=session['user_id'])).first()
                    if not saved_address:
                        return jsonify({'success': False, 'message': 'Invalid billing address selected', 'errors': {'billing_address_id': 'Invalid address selected'}}), 400
                    user = db.session.scalars(db.select(Users).filter_by(id=session['user_id'])).first()
                    user_email = user.email if user else ''
                    billing_address = {
                        'first_name': saved_address.full_name.split(' ')[0] if saved_address.full_name else '',
                        'last_name': ' '.join(saved_address.full_name.split(' ')[1:]) if saved_address.full_name else '',
                        'email': saved_address.email or user_email,
                        'phone': saved_address.mobile_number,
                        'address1': saved_address.address_line1,
                        'address2': saved_address.address_line2 or '',
                        'city': saved_address.city,
                        'state': saved_address.state,
                        'zip_code': saved_address.postal_code,
                        'state_code': saved_address.state_code or '',
                        'company_name': saved_address.company_name or '',
                        'gst_number': saved_address.gst_number or '',
                        'address_type': saved_address.address_type or 'Home'
                    }
            
            # Automatically update empty profile fields (first_name, last_name, mobile_number) from entered address
            current_user = db.session.scalars(db.select(Users).filter_by(id=session['user_id'])).first()
            if current_user:
                if (not current_user.first_name or current_user.first_name == '') and shipping_address.get('first_name'):
                    current_user.first_name = shipping_address['first_name']
                if (not current_user.last_name or current_user.last_name == '') and shipping_address.get('last_name'):
                    current_user.last_name = shipping_address['last_name']
                if (not current_user.mobile_number or current_user.mobile_number == '') and shipping_address.get('phone'):
                    current_user.mobile_number = shipping_address['phone']
                
            subtotal = sum(float(item.get('price') or item.get('unit_price') or 0) * int(item['quantity']) for item in cart_items)
            free_threshold = float(get_config('FREE_SHIPPING_THRESHOLD', 500) or 500)
            default_charge = float(get_config('DEFAULT_SHIPPING_CHARGE', 0) or 0)
            shipping_charge = 0.00 if subtotal >= free_threshold else default_charge
            discount_amount = 0.00
            coupon_code = request.form.get('coupon_code', '').strip().upper()
            is_valid_coupon = False
            if coupon_code:
                coupon = db.session.scalars(
                    db.select(Coupons).filter(Coupons.code == coupon_code, Coupons.is_active == 1, Coupons.expiry > db.func.now())
                ).first()
                if coupon:
                    min_order = float(coupon.min_order or 0)
                    disc_val = float(coupon.discount_value or 0)
                    disc_type = coupon.discount_type.value if hasattr(coupon.discount_type, 'value') else str(coupon.discount_type)
                    if subtotal >= min_order:
                        is_valid_coupon = True
                        if disc_type == 'percentage':
                            discount_amount = subtotal * (disc_val / 100.0)
                        else:
                            discount_amount = disc_val
            
            gift_card_code = request.form.get('gift_card_code', '').strip().upper()
            use_loyalty_points = request.form.get('use_loyalty_points') == '1'
            points_to_redeem_str = request.form.get('points_to_redeem')
            points_to_redeem = None
            if points_to_redeem_str and points_to_redeem_str.strip():
                try:
                    points_to_redeem = int(points_to_redeem_str)
                except ValueError:
                    pass

            gift_card_id = None
            gift_card_discount = 0.0
            loyalty_points_used = 0
            loyalty_discount = 0.0

            total = subtotal + shipping_charge - discount_amount

            # Process Loyalty Points
            if use_loyalty_points and total > 0:
                user_points = db.session.scalar(db.select(db.func.sum(LoyaltyLedger.points)).filter_by(user_id=session['user_id'])) or 0
                if user_points > 0:
                    pts = points_to_redeem if points_to_redeem and points_to_redeem > 0 else int(user_points)
                    pts = min(pts, int(user_points))
                    max_points_for_total = int(total * 4)
                    pts = min(pts, max_points_for_total)
                    
                    if pts > 0:
                        loyalty_discount = pts / 4.0
                        loyalty_points_used = pts
                        total -= loyalty_discount
                        discount_amount += loyalty_discount  # Add to total discount

            # Process Gift Card
            if gift_card_code and total > 0:
                gc = db.session.scalars(
                    db.select(GiftCards).filter(
                        GiftCards.code == gift_card_code,
                        GiftCards.is_active == 1,
                        or_(GiftCards.expiry_date == None, GiftCards.expiry_date > db.func.now())
                    )
                ).first()
                if gc and float(gc.current_balance or 0) > 0:
                    gift_card_id = gc.id
                    gift_card_discount = min(total, float(gc.current_balance))
                    total -= gift_card_discount
                    discount_amount += gift_card_discount # Add to total discount

            order = Orders(
                user_id=session['user_id'],
                total_amount=total,
                subtotal=float(subtotal),
                payment_method=request.form.get('payment_method'),
                payment_status='pending',
                billing_address=json.dumps(billing_address),
                shipping_address=json.dumps(shipping_address),
                shipping_charge=shipping_charge,
                discount_amount=discount_amount,
                coupon_code=coupon_code if is_valid_coupon else None,
                status='pending',
                order_dateonly=db.func.current_date(),
                loyalty_points_used=loyalty_points_used,
                gift_card_id=gift_card_id,
                gift_card_discount=gift_card_discount
            )
            db.session.add(order)
            db.session.flush() # To get order.id
            order_id = order.id
            
            if loyalty_points_used > 0:
                ledger_entry = LoyaltyLedger(
                    user_id=session['user_id'],
                    points=-loyalty_points_used,
                    transaction_type='redeemed',
                    order_id=order_id
                )
                db.session.add(ledger_entry)
                
            if gift_card_id and gift_card_discount > 0:
                gc = db.session.get(GiftCards, gift_card_id)
                if gc:
                    from decimal import Decimal
                    gc.current_balance = Decimal(str(float(gc.current_balance or 0) - gift_card_discount))
                gc_txn = GiftCardTransactions(
                    gift_card_id=gift_card_id,
                    order_id=order_id,
                    amount_used=gift_card_discount
                )
                db.session.add(gc_txn)

            if request.form.get('payment_method') == 'online':
                session['pending_online_order_id'] = order_id

            company = db.session.scalars(db.select(CompanyInfo).limit(1)).first()
            price_includes_gst = company.price_includes_gst if company else True
            company_state = str(company.state_code or '').strip().strip('"\'') if company else ''
            
            customer_state = str(billing_address.get('state_code', '')).strip().strip('"\'')
            is_intra_state = (company_state == customer_state)

            discount_percentage = Decimal(0)
            if discount_amount > 0 and subtotal > 0:
                discount_percentage = (Decimal(str(discount_amount)) / Decimal(str(subtotal))) * Decimal(100)

            total_order_taxable = Decimal(0)
            total_order_cgst = Decimal(0)
            total_order_sgst = Decimal(0)
            total_order_igst = Decimal(0)
            total_order_gst = Decimal(0)

            for item in cart_items:
                item_unit_price = Decimal(str(item.get('price', item.get('unit_price', 0))))
                item_quantity = Decimal(str(item.get('quantity', 1)))
                gst_rate = Decimal(str(item.get('gst_rate', 18))) if item.get('gst_rate') else Decimal('18.00')
                item_discount = (item_unit_price * item_quantity) * (discount_percentage / Decimal(100))
                
                if price_includes_gst:
                    taxable_value = (item_unit_price * item_quantity - item_discount) / (Decimal('1') + gst_rate / Decimal('100'))
                else:
                    taxable_value = item_unit_price * item_quantity - item_discount
                    
                if is_intra_state:
                    cgst = (taxable_value * (gst_rate / Decimal(2))) / Decimal(100)
                    sgst = (taxable_value * (gst_rate / Decimal(2))) / Decimal(100)
                    igst = Decimal(0)
                else:
                    cgst = sgst = Decimal(0)
                    igst = (taxable_value * gst_rate) / Decimal(100)

                order_item = OrderItems(
                    order_id=order_id,
                    product_id=item['id'],
                    product_name=item['name'],
                    price=float(item_unit_price),
                    quantity=int(item_quantity),
                    gst_rate=float(gst_rate),
                    cgst_amount=float(cgst),
                    sgst_amount=float(sgst),
                    igst_amount=float(igst),
                    taxable_value=float(taxable_value),
                    hsn_code=item.get('hsn_code')
                )
                db.session.add(order_item)
                
                total_order_taxable += taxable_value
                total_order_cgst += cgst
                total_order_sgst += sgst
                total_order_igst += igst
                total_order_gst += (cgst + sgst + igst)
                
                if request.form.get('payment_method') == 'cod':
                    product = item['product_obj']
                    previous_qty = product.stock_quantity or 0

                    if item['quantity'] > previous_qty:
                        raise Exception(f"Insufficient stock for {item['name']}. Please try again.")
                    
                    product.stock_quantity = previous_qty - item['quantity']

                    # Log the adjustment to inventory_logs
                    new_qty = previous_qty - item['quantity']
                    inv_log = InventoryLogs(
                        product_id=item['id'],
                        previous_quantity=previous_qty,
                        adjustment=-item['quantity'],
                        new_quantity=new_qty,
                        notes=f"Stock decremented for COD Order #{order_id}",
                        adjusted_by="system",
                        adjustment_type="order",
                        reference_id=str(order_id)
                    )
                    db.session.add(inv_log)

            # Update the order with total GST amounts
            from decimal import Decimal
            order.taxable_amount = Decimal(str(total_order_taxable))
            order.cgst_amount = Decimal(str(total_order_cgst))
            order.sgst_amount = Decimal(str(total_order_sgst))
            order.igst_amount = Decimal(str(total_order_igst))
            order.total_gst = Decimal(str(total_order_gst))

            if request.form.get('payment_method') == 'cod':
                db.session.execute(db.delete(Cart).where(Cart.user_id == session['user_id']))

            # Save shipping address if requested
            if request.form.get('save_shipping_address'):
                try:
                    full_name_val = f"{shipping_address['first_name']} {shipping_address['last_name']}".strip()
                    existing_shipping = db.session.scalars(
                        db.select(UserAddresses).filter_by(
                            user_id=session['user_id'],
                            full_name=full_name_val,
                            mobile_number=shipping_address['phone'],
                            address_line1=shipping_address['address1'],
                            city=shipping_address['city'],
                            state=shipping_address['state'],
                            postal_code=shipping_address['zip_code']
                        )
                    ).first()
                    
                    if not existing_shipping:
                        is_shipping_default = 1 if request.form.get('set_shipping_default') else 0
                        if is_shipping_default:
                            db.session.execute(db.update(UserAddresses).where(UserAddresses.user_id == session['user_id']).values(is_default=0))
                        
                        new_addr = UserAddresses(
                            user_id=session['user_id'],
                            address_type=request.form.get('address_type') or 'Home',
                            full_name=full_name_val,
                            mobile_number=shipping_address['phone'],
                            address_line1=shipping_address['address1'],
                            address_line2=shipping_address['address2'] or None,
                            city=shipping_address['city'],
                            state=shipping_address['state'],
                            postal_code=shipping_address['zip_code'],
                            is_default=is_shipping_default,
                            state_code=request.form.get('shipping_state_code'),
                            email=shipping_address['email']
                        )
                        db.session.add(new_addr)
                except Exception as e:
                    logging.info(f"Error saving shipping address: {str(e)}")

            # Save billing address if requested and different from shipping
            if not same_as_shipping and request.form.get('save_billing_address'):
                try:
                    full_name_bill = f"{billing_address['first_name']} {billing_address['last_name']}".strip()
                    existing_billing = db.session.scalars(
                        db.select(UserAddresses).filter_by(
                            user_id=session['user_id'],
                            full_name=full_name_bill,
                            mobile_number=billing_address['phone'],
                            address_line1=billing_address['address1'],
                            city=billing_address['city'],
                            state=billing_address['state'],
                            postal_code=billing_address['zip_code']
                        )
                    ).first()

                    if not existing_billing:
                        is_billing_default = 1 if request.form.get('set_billing_default') else 0
                        if is_billing_default:
                            db.session.execute(db.update(UserAddresses).where(UserAddresses.user_id == session['user_id']).values(is_default=0))
                        
                        new_bill_addr = UserAddresses(
                            user_id=session['user_id'],
                            address_type='bill',
                            full_name=full_name_bill,
                            mobile_number=billing_address['phone'],
                            address_line1=billing_address['address1'],
                            address_line2=billing_address['address2'] or None,
                            city=billing_address['city'],
                            state=billing_address['state'],
                            postal_code=billing_address['zip_code'],
                            is_default=is_billing_default,
                            state_code=request.form.get('billing_state_code'),
                            email=billing_address['email']
                        )
                        db.session.add(new_bill_addr)
                except Exception as e:
                    logging.info(f"Error saving billing address: {str(e)}")

            phonepe_client = current_app.extensions.get('phonepe_client') or getattr(current_app, 'phonepe_client', None)
            if request.form.get('payment_method') == 'online' and not phonepe_client:
                db.session.rollback()
                return jsonify({'success': False, 'message': 'Online payment gateway is currently unavailable. Please choose Cash on Delivery or try again later.'}), 503
            
            if request.form.get('payment_method') == 'online' and phonepe_client:
                try:
                    merchant_order_id = f"OR_{order_id}"
                    amount_paisa = int(total * 100)

                    meta_info = MetaInfo(
                        udf1=f"{shipping_address['first_name']} {shipping_address['last_name']}",
                        udf2=shipping_address['email']
                    )

                    prefill_details = PrefillUserLoginDetails(
                        phone_number=shipping_address['phone']
                    )

                    pay_request = StandardCheckoutPayRequest.build_request(
                        merchant_order_id=merchant_order_id,
                        amount=amount_paisa,
                        redirect_url=url_for('checkout_bp.phonepe_response', _external=True),
                        meta_info=meta_info,  # type: ignore
                        prefill_user_login_details=prefill_details,
                        message=f"Online Payment - Aanyaas Enterprises",
                        expire_after=3600,
                        disable_payment_retry=False
                    )

                    if os.getenv('PHONEPE_ENV') != 'PRODUCTION':
                        logging.info(f"Initiating PhonePe Pay for {merchant_order_id}, Amount: {amount_paisa}")

                    pay_response = phonepe_client.pay(pay_request)
                    if pay_response.redirect_url:
                        db.session.commit()
                        return jsonify({'success': True, 'redirect': pay_response.redirect_url})
                    else:
                        raise Exception("Failed to get redirect URL from PhonePe")
                except Exception as pe_error:
                    logging.info(f"PhonePe Initiation Error for Order {order_id} (User {session.get('user_id')}): {str(pe_error)}")
                    db.session.rollback()
                    return jsonify({'success': False, 'message': f'Payment initiation failed: {str(pe_error)}'}), 500

            db.session.commit()
            try:
                trigger_all_order_notifications(order_id, 'placed')
            except Exception as notify_e:
                logging.info(f"Failed to trigger notifications for order {order_id}: {notify_e}")
            return jsonify({'success': True, 'redirect': url_for('checkout_bp.order_confirmation', order_id=order_id)})

        except Exception as e:
            db.session.rollback()
            logging.info(f"Checkout error: {str(e)}")
            traceback.print_exc()
            return jsonify({'success': False, 'message': f'Error during checkout: {str(e)}'}), 500
    else:  # GET request
        try:
            from models import Cart, Products, UserAddresses, Coupons, Users, LoyaltyLedger
            
            cart_query = db.session.execute(
                db.select(Cart, Products).join(Products, Cart.product_id == Products.id).filter(Cart.user_id == session['user_id'])
            ).all()
            
            cart_items = []
            for cart_item, product in cart_query:
                cart_items.append({
                    'id': product.id,
                    'name': product.name,
                    'price': float(product.price or 0),
                    'image': product.image,
                    'quantity': cart_item.quantity,
                    'stock_quantity': product.stock_quantity
                })
                
            cart_items = process_products(cart_items)
            if not cart_items:
                return redirect(url_for('cart_bp.cart'))

            user_addresses_obj = db.session.scalars(db.select(UserAddresses).filter_by(user_id=session['user_id']).order_by(UserAddresses.is_default.desc(), UserAddresses.address_type)).all()
            user_addresses = []
            for addr in user_addresses_obj:
                user_addresses.append({
                    'id': addr.id,
                    'user_id': addr.user_id,
                    'address_type': addr.address_type,
                    'full_name': addr.full_name,
                    'mobile_number': addr.mobile_number,
                    'email': addr.email,
                    'address_line1': addr.address_line1,
                    'address_line2': addr.address_line2,
                    'city': addr.city,
                    'state': addr.state,
                    'state_code': addr.state_code,
                    'postal_code': addr.postal_code,
                    'company_name': addr.company_name,
                    'gst_number': addr.gst_number,
                    'is_default': addr.is_default
                })

            coupon_code = session.get('applied_coupon')
            db_coupons = db.session.scalars(db.select(Coupons).filter(Coupons.is_active == 1, Coupons.expiry > db.func.now())).all()
            
            available_coupons = {
                c.code: {
                    'discount': float(c.discount_value or 0),
                    'type': c.discount_type.value if hasattr(c.discount_type, 'value') else str(c.discount_type),
                    'min_order': float(c.min_order or 0)
                } for c in db_coupons
            }
            subtotal = sum(item['item_total'] for item in cart_items)

            free_threshold = float(get_config('FREE_SHIPPING_THRESHOLD', 500) or 500)
            default_ship = float(get_config('DEFAULT_SHIPPING_CHARGE', 120) or 120)

            shipping_charge = 0.00 if subtotal >= free_threshold else default_ship
            discount_amount = 0.00
            if coupon_code and coupon_code in available_coupons:
                coupon = available_coupons[coupon_code]
                if subtotal >= coupon['min_order']:
                    if coupon['type'] == 'percentage':
                        discount_amount = subtotal * (coupon['discount'] / 100.0)
                    else:
                        discount_amount = coupon['discount']
                    discount_amount = min(discount_amount, subtotal)
                else:
                    session.pop('applied_coupon', None)
                    coupon_code = None
            total = subtotal + shipping_charge - discount_amount

            user = db.session.scalars(db.select(Users).filter_by(id=session['user_id'])).first()
            if not user:
                session.clear()
                flash('User session expired. Please login again.', 'error')
                return redirect(url_for('auth_bp.login'))

            user_loyalty_points = int(db.session.scalar(db.select(db.func.sum(LoyaltyLedger.points)).filter_by(user_id=session['user_id'])) or 0)

            return render_template('checkout.html', user=user, cart_items=cart_items, user_addresses=user_addresses,
                                   subtotal=subtotal, shipping_charge=shipping_charge, discount_amount=discount_amount,
                                   total=total, free_shipping_threshold=free_threshold,
                                   default_shipping_charge=default_ship,
                                   available_coupons=available_coupons, coupon_code=coupon_code or '', 
                                   user_logged_in=True, username=session.get("username"),
                                   user_loyalty_points=user_loyalty_points)
        except Exception as e:
            logging.info(f"Checkout GET error: {str(e)}")
            traceback.print_exc()
            flash('Error loading checkout page.', 'error')
            return redirect(url_for('cart_bp.cart'))

@checkout_bp.route('/checkout-drawer', methods=['GET'])
@login_required
def checkout_drawer():
    try:
        from models import Cart, Products, UserAddresses, Coupons, Users
        user_id = session['user_id']
        
        # Fetch cart items joined with products
        cart_records = db.session.execute(
            db.select(Cart, Products)
            .join(Products, Cart.product_id == Products.id)
            .filter(Cart.user_id == user_id)
        ).all()
        
        cart_items_raw = []
        for cart_item, product in cart_records:
            cart_items_raw.append({
                'id': product.id,
                'name': product.name,
                'price': product.price,
                'image': product.image,
                'quantity': cart_item.quantity,
                'stock_quantity': product.stock_quantity
            })
            
        cart_items = process_products(cart_items_raw)
        if not cart_items:
            return "Your cart is empty."

        # Fetch user addresses
        addresses = db.session.scalars(
            db.select(UserAddresses)
            .filter_by(user_id=user_id)
            .order_by(UserAddresses.is_default.desc(), UserAddresses.address_type)
        ).all()
        
        user_addresses = []
        for addr in addresses:
            addr_dict = {
                'id': addr.id,
                'user_id': addr.user_id,
                'full_name': addr.full_name,
                'address_line1': addr.address_line1,
                'address_line2': addr.address_line2,
                'city': addr.city,
                'state': addr.state,
                'postal_code': addr.postal_code,
                'pincode': addr.postal_code,
                'mobile_number': addr.mobile_number,
                'mobile': addr.mobile_number,
                'email': addr.email,
                'address_type': addr.address_type,
                'is_default': addr.is_default,
                'created_at': addr.created_at.isoformat() if addr.created_at else None,
                'updated_at': addr.updated_at.isoformat() if addr.updated_at else None,
                'state_code': addr.state_code,
                'gst_number': addr.gst_number,
                'company_name': addr.company_name
            }
            user_addresses.append(addr_dict)

        coupon_code = session.get('applied_coupon')
        
        # Fetch active coupons
        active_coupons = db.session.scalars(
            db.select(Coupons)
            .filter(and_(Coupons.is_active == True, Coupons.expiry > db.func.now()))
        ).all()
        
        available_coupons = {
            c.code: {
                'discount': float(c.discount_value),
                'type': c.discount_type,
                'min_order': float(c.min_order)
            } for c in active_coupons
        }
        
        subtotal = sum(item['item_total'] for item in cart_items)

        free_threshold = float(get_config('FREE_SHIPPING_THRESHOLD', 500) or 500)
        default_ship = float(get_config('DEFAULT_SHIPPING_CHARGE', 120) or 120)

        shipping_charge = 0.00 if subtotal >= free_threshold else default_ship
        discount_amount = 0.00
        if coupon_code and coupon_code in available_coupons:
            coupon = available_coupons[coupon_code]
            if subtotal >= coupon['min_order']:
                if coupon['type'] == 'percentage':
                    discount_amount = subtotal * (coupon['discount'] / 100.0)
                else:
                    discount_amount = coupon['discount']
                discount_amount = min(discount_amount, subtotal)
            else:
                session.pop('applied_coupon', None)
                coupon_code = None
        total = subtotal + shipping_charge - discount_amount

        user_obj = db.session.scalars(db.select(Users).filter_by(id=user_id)).first()
        if not user_obj:
            session.clear()
            return redirect(url_for('auth_bp.login'))
            
        user = user_obj
        
        from models import LoyaltyLedger
        user_loyalty_points = int(db.session.scalar(db.select(db.func.sum(LoyaltyLedger.points)).filter_by(user_id=user_id)) or 0)

        return render_template('checkout_drawer.html', user=user, cart_items=cart_items, user_addresses=user_addresses,
                               subtotal=subtotal, shipping_charge=shipping_charge, discount_amount=discount_amount,
                               total=total, free_shipping_threshold=free_threshold, user_loyalty_points=user_loyalty_points,
                               default_shipping_charge=default_ship,
                               available_coupons=available_coupons, coupon_code=coupon_code or '')
    except Exception as e:
        logging.info(f"Checkout Drawer error: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Error loading checkout: {str(e)}"

@checkout_bp.route("/order-confirmation/<int:order_id>")
@login_required
def order_confirmation(order_id):
    try:
        order_obj = db.session.scalars(db.select(Orders).filter_by(id=order_id, user_id=session['user_id'])).first()
        if not order_obj:
            flash('Order not found', 'error')
            return redirect(url_for('pages_bp.home'))
            
        user = db.session.scalars(db.select(Users).filter_by(id=session['user_id'])).first()
        
        # Convert to dict to match template expectations
        order = {
            'id': order_obj.id,
            'invoice_number': order_obj.invoice_number,
            'user_id': order_obj.user_id,
            'total_amount': order_obj.total_amount,
            'order_date': order_obj.order_date,
            'status': order_obj.status,
            'accepted_at': order_obj.accepted_at,
            'shipped_at': order_obj.shipped_at,
            'delivered_at': order_obj.delivered_at,
            'courier_name': order_obj.courier_name,
            'tracking_id': order_obj.tracking_id,
            'subtotal': order_obj.subtotal,
            'shipping_charge': order_obj.shipping_charge or 0,
            'discount_amount': order_obj.discount_amount or 0,
            'coupon_code': order_obj.coupon_code,
            'total_gst': order_obj.total_gst or 0,
            'payment_method': order_obj.payment_method,
            'payment_status': order_obj.payment_status,
            'taxable_amount': order_obj.taxable_amount or 0,
            'cgst_amount': order_obj.cgst_amount or 0,
            'sgst_amount': order_obj.sgst_amount or 0,
            'igst_amount': order_obj.igst_amount or 0,
            'loyalty_points_used': order_obj.loyalty_points_used or 0,
            'gift_card_discount': order_obj.gift_card_discount or 0,
            'email': user.email if user else '',
            'user_email': user.email if user else ''
        }
        
        try:
            billing_address = json.loads(order_obj.billing_address) if order_obj.billing_address else {}
            if 'email' not in billing_address:
                billing_address['email'] = order.get('user_email', '')
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logging.info(f"Error parsing billing address: {str(e)}")
            billing_address = {'error': 'Could not load billing address', 'first_name': '', 'last_name': '', 'email': order.get('email', ''), 'phone': '', 'address1': '', 'address2': '', 'city': '', 'state': '', 'zip_code': '', 'country': 'India'}
        try:
            shipping_address = json.loads(order_obj.shipping_address) if order_obj.shipping_address else {}
            if 'email' not in shipping_address:
                shipping_address['email'] = order.get('user_email', '')
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logging.info(f"Error parsing shipping address: {str(e)}")
            shipping_address = {'error': 'Could not load shipping address', 'first_name': '', 'last_name': '', 'email': order.get('email', ''), 'phone': '', 'address1': '', 'address2': '', 'city': '', 'state': '', 'zip_code': '', 'country': 'India'}
            
        items_query = db.session.query(OrderItems, Products).join(Products, OrderItems.product_id == Products.id).filter(OrderItems.order_id == order_id).all()
        items = []
        for oi, p in items_query:
            returned_qty = db.session.query(db.func.coalesce(db.func.sum(ReturnItems.quantity), 0))\
                .join(OrderReturns, ReturnItems.return_id == OrderReturns.id)\
                .filter(OrderReturns.order_id == order_id, ReturnItems.product_id == oi.product_id, OrderReturns.status != 'rejected').scalar()
            
            items.append({
                'id': oi.id,
                'order_id': oi.order_id,
                'product_id': oi.product_id,
                'quantity': oi.quantity,
                'price': oi.price,
                'name': p.name,
                'image': p.image,
                'returned_qty': returned_qty
            })
            
        is_returnable = any((item['quantity'] - item['returned_qty']) > 0 for item in items)

        active_return_count = db.session.query(OrderReturns).filter(OrderReturns.order_id == order_id, ~OrderReturns.status.in_(['completed', 'rejected'])).count()
        has_active_return = active_return_count > 0

        order_date = order['order_date']
        if isinstance(order_date, str):
            try:
                order_date = datetime.strptime(order_date, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                order_date = datetime.now()
        order_date = utc_to_ist(order_date)

        def parse_datetime(value):
            if isinstance(value, str):
                try:
                    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    return None
            return value

        accepted_at = utc_to_ist(parse_datetime(order.get('accepted_at')))
        shipped_at  = utc_to_ist(parse_datetime(order.get('shipped_at')))
        delivered_at = utc_to_ist(parse_datetime(order.get('delivered_at')))

        estimated_accepted_date = (order_date or datetime.now()) + timedelta(days=1)
        estimated_shipping_date = (order_date or datetime.now()) + timedelta(days=2)
        estimated_delivery_date = (order_date or datetime.now()) + timedelta(days=5)

        def format_date(date_value, estimated_date):
            if date_value:
                return date_value.strftime('%d %b %Y')
            return estimated_date.strftime('%d %b %Y')

        accepted_date = format_date(accepted_at, estimated_accepted_date)
        shipping_date = format_date(shipped_at, estimated_shipping_date)
        delivery_date = format_date(delivered_at, estimated_delivery_date)

        status_steps = {
            'placed': True,
            'accepted': accepted_at is not None,
            'shipped': shipped_at is not None,
            'delivered': delivered_at is not None
        }
        gst_data = calculate_gst_breakdown(order_id)
        if 'items' not in gst_data or not isinstance(gst_data['items'], (list, dict)):
            gst_data['items'] = []
        return render_template('order_confirmation.html',
                               order=order, billing_address=billing_address, shipping_address=shipping_address,
                               items=items, has_invoice_download=check_if_invoice_available(order_id),
                               order_date=order_date, accepted_date=accepted_date, shipping_date=shipping_date,
                               delivery_date=delivery_date, status_steps=status_steps,
                               courier_name=order.get('courier_name', ''), tracking_id=order.get('tracking_id', ''),
                               gst_breakdown=gst_data, user_logged_in=True, username=session.get("username"), is_returnable=is_returnable, has_active_return=has_active_return)
    except Exception as e:
        db.session.rollback()
        logging.info(f"Order confirmation error via ORM: {str(e)}")
        traceback.print_exc()
        flash('Error retrieving order details', 'error')
        return redirect(url_for('pages_bp.home'))

@checkout_bp.route('/phonepe/response', methods=['GET', 'POST'])
@login_required
def phonepe_response():
    merchant_order_id = request.args.get("merchantOrderId") or request.form.get("merchantOrderId")
    phonepe_client = current_app.extensions.get('phonepe_client') or getattr(current_app, 'phonepe_client', None)
    if not merchant_order_id:
        _pid = session.pop('pending_online_order_id', None)
        session.pop('pending_online_order', None)
        if _pid:
            _mid_check = f"OR_{_pid}"
            if phonepe_client:
                try:
                    _sts = phonepe_client.get_order_status(_mid_check, details=False)
                    if _sts.state == 'COMPLETED':
                        finalize_successful_order(_pid, _mid_check)
                        flash('Payment successful! Your order has been placed.', 'success')
                        return redirect(url_for('checkout_bp.order_confirmation', order_id=_pid))
                except Exception as _ce:
                    logging.info(f'PhonePe check error: {_ce}')
            
            # If not completed or exception, cancel the order
            cancel_failed_order(_pid, "User abandoned checkout before completion")
        
        flash('Payment was cancelled or could not be verified. Please try again or choose Cash on Delivery.', 'warning')
        return redirect(url_for('shop_bp.shop', open_checkout=1))

    order_id = int(merchant_order_id.replace('OR_', ''))

    if not phonepe_client:
        flash('Payment system unavailable. Please contact support.', 'error')
        return redirect(url_for('pages_bp.home'))

    try:
        status_response = phonepe_client.get_order_status(merchant_order_id, details=False)

        if status_response.state == 'COMPLETED':
            finalize_successful_order(order_id, merchant_order_id)
            session.pop('pending_online_order_id', None)
            session.pop('pending_online_order', None)
            flash('Payment successful! Your order has been placed.', 'success')
            return redirect(url_for('checkout_bp.order_confirmation', order_id=order_id))
        else:
            cancel_failed_order(order_id, f"Payment failed with state: {status_response.state}")
            flash('Your payment was not completed. Please try again or choose Cash on Delivery.', 'warning')
            return redirect(url_for('shop_bp.shop', open_checkout=1))
    except Exception as e:
        logging.info(f"PhonePe Response Error: {str(e)}")
        flash('Error verifying payment status.', 'error')
        return redirect(url_for('pages_bp.home'))

@checkout_bp.route('/initiate-phonepe-payment/<int:order_id>')
def initiate_phonepe_payment(order_id):
    if 'user_id' not in session:
        flash('Please login to continue.', 'error')
        return redirect(url_for('auth_bp.login'))
    phonepe_client = current_app.extensions.get('phonepe_client') or getattr(current_app, 'phonepe_client', None)
    if not phonepe_client:
        flash('Online payment is currently unavailable. Please choose Cash on Delivery.', 'error')
        return redirect(url_for('checkout_bp.payment_cancelled', order_id=order_id))
    try:
        from models import Orders, Users
        order = db.session.scalars(db.select(Orders).filter_by(id=order_id, user_id=session['user_id'])).first()
        if not order:
            flash('Order not found or unauthorized.', 'error')
            return redirect(url_for('pages_bp.home'))
        user = db.session.scalars(db.select(Users).filter_by(id=order.user_id)).first()
        merchant_order_id = f"OR_{order_id}"
        amount_paisa = int(float(order.total_amount) * 100)
        meta_info = CreateSdkOrderRequest(merchant_order_id=merchant_order_id, amount=amount_paisa)
        prefill_details = PrefillUserLoginDetails(phone_number=user.phone if user and hasattr(user, 'phone') else '')
        pay_request = StandardCheckoutPayRequest.build_request(
            merchant_order_id=merchant_order_id,
            amount=amount_paisa,
            redirect_url=url_for('checkout_bp.phonepe_response', _external=True),
            meta_info=meta_info,  # type: ignore
            prefill_user_login_details=prefill_details,
            message=f"Retry Payment for Order #{order_id}",
            expire_after=3600,
            disable_payment_retry=False
        )
        pay_response = phonepe_client.pay(pay_request)
        if pay_response.redirect_url:
            return redirect(pay_response.redirect_url)
        else:
            raise Exception("Failed to get redirect URL from PhonePe")
    except Exception as e:
        flash(f'Could not initiate payment: {str(e)}. Please try again or use Cash on Delivery.', 'error')
        return redirect(url_for('checkout_bp.payment_cancelled', order_id=order_id))

@checkout_bp.route('/payment-cancelled/<int:order_id>')
def payment_cancelled(order_id):
    try:
        from models import Orders, Users
        order = db.session.scalars(db.select(Orders).filter_by(id=order_id)).first()
        if not order:
            flash('Order not found.', 'error')
            return redirect(url_for('pages_bp.home'))
        user = db.session.scalars(db.select(Users).filter_by(id=order.user_id)).first()
        order_dict = {
            'id': order.id,
            'total_amount': order.total_amount,
            'email': user.email if user else '',
            'full_name': user.full_name if user else ''
        }
        return render_template('payment_cancelled.html', order=order_dict)
    except Exception:
        flash('An error occurred. Please try again.', 'error')
        return redirect(url_for('checkout_bp.checkout'))

@checkout_bp.route('/switch-to-cod/<int:order_id>', methods=['POST'])
def switch_to_cod(order_id):
    if 'user_id' not in session:
        flash('Please login to continue.', 'error')
        return redirect(url_for('auth_bp.login'))
    try:
        from models import Orders
        order = db.session.scalars(db.select(Orders).filter_by(id=order_id, user_id=session['user_id'])).first()
        if not order:
            flash('Order not found or unauthorized.', 'error')
            return redirect(url_for('pages_bp.home'))
        order.payment_method = 'cod'
        order.payment_status = 'pending'
        order.status = 'processing'
        db.session.commit()
        flash('Your order has been placed with Cash on Delivery. Thank you!', 'success')
        return redirect(url_for('checkout_bp.order_confirmation', order_id=order_id))
    except Exception:
        db.session.rollback()
        flash('An error occurred. Please try again.', 'error')
        return redirect(url_for('checkout_bp.checkout'))

@checkout_bp.route('/phonepe/webhook', methods=['POST'])
@csrf_exempt
def phonepe_webhook():
    """PhonePe Webhook (S2S Callback) handler that returns 200 OK immediately under all circumstances."""
    phonepe_client = current_app.extensions.get('phonepe_client') or getattr(current_app, 'phonepe_client', None)
    try:
        if not phonepe_client:
            logging.info("PhonePe Webhook: Client not initialized, returning 200 OK")
            return jsonify({'success': False, 'message': 'Client not initialized'}), 200

        callback_body = request.get_data(as_text=True)
        auth_header = request.headers.get('Authorization')

        callback_user = os.getenv('PHONEPE_CALLBACK_USERNAME')
        callback_pass = os.getenv('PHONEPE_CALLBACK_PASSWORD')

        callback_res = phonepe_client.validate_callback(
            username=callback_user,
            password=callback_pass,
            callback_header_data=auth_header,
            callback_response_data=callback_body
        )

        event_type = callback_res.event
        payload = callback_res.payload
        merchant_order_id = getattr(payload, 'original_merchant_order_id', None) or getattr(payload, 'merchantOrderId', None)

        if merchant_order_id:
            order_id = int(merchant_order_id.replace('OR_', ''))
            from models import Orders
            
            order = db.session.scalars(db.select(Orders).filter_by(id=order_id)).first()
            if order:
                if event_type == 'checkout.order.completed' and payload.state == 'COMPLETED':
                    finalize_successful_order(order_id, merchant_order_id)
                elif event_type == 'checkout.order.failed' or payload.state == 'FAILED':
                    cancel_failed_order(order_id, "Webhook reported failure")
                elif event_type == 'pg.refund.completed':
                    order.refund_status = 'COMPLETED'
                    db.session.commit()
                elif event_type == 'pg.refund.failed':
                    order.refund_status = 'FAILED'
                    db.session.commit()

            logging.info(f"PhonePe Webhook Processed Successfully: Order {order_id}, Event: {event_type}")

        return jsonify({'success': True}), 200

    except PhonePeException as pe_ex:
        logging.info(f"PhonePe Webhook Validation Failed: {pe_ex.message}")
        return jsonify({'success': False, 'message': 'Validation failed'}), 200
    except Exception as e:
        db.session.rollback()
        logging.info(f"PhonePe Webhook Error: {str(e)}")
        return jsonify({'success': False}), 200

@checkout_bp.route('/phonepe/callback', methods=['POST'])
@csrf_exempt
def phonepe_callback():
    phonepe_client = current_app.extensions.get('phonepe_client') or getattr(current_app, 'phonepe_client', None)
    if not phonepe_client:
        return jsonify({'success': False, 'message': 'Client not initialized'}), 500

    try:
        callback_body = request.get_data(as_text=True)
        auth_header = request.headers.get('Authorization')

        callback_user = os.getenv('PHONEPE_CALLBACK_USERNAME')
        callback_pass = os.getenv('PHONEPE_CALLBACK_PASSWORD')

        callback_res = phonepe_client.validate_callback(
            username=callback_user,
            password=callback_pass,
            callback_header_data=auth_header,
            callback_response_data=callback_body
        )

        event_type = callback_res.event
        payload = callback_res.payload
        merchant_order_id = getattr(payload, 'original_merchant_order_id', None) or getattr(payload, 'merchantOrderId', None)

        if merchant_order_id:
            order_id = int(merchant_order_id.replace('OR_', ''))
            from models import Orders

            order = db.session.scalars(db.select(Orders).filter_by(id=order_id)).first()
            if order:
                if event_type == 'checkout.order.completed' and payload.state == 'COMPLETED':
                    finalize_successful_order(order_id, merchant_order_id)
                elif event_type == 'checkout.order.failed' or payload.state == 'FAILED':
                    cancel_failed_order(order_id, "Callback reported failure")
                elif event_type == 'pg.refund.completed':
                    order.refund_status = 'COMPLETED'
                    db.session.commit()
                elif event_type == 'pg.refund.failed':
                    order.refund_status = 'FAILED'
                    db.session.commit()

        return jsonify({'success': True}), 200

    except PhonePeException as pe_ex:
        logging.info(f"PhonePe Webhook Validation Failed: {pe_ex.message}")
        return jsonify({'success': False, 'message': 'Validation failed'}), 401
    except Exception as e:
        db.session.rollback()
        logging.info(f"PhonePe Webhook Error: {str(e)}")
        return jsonify({'success': False}), 500

@checkout_bp.route('/cancel-order', methods=['POST'])
@login_required
def cancel_order():
    try:
        order_id = request.form.get('order_id', type=int)
        reason = request.form.get('reason', '').strip()
        if not order_id:
            return jsonify({'success': False, 'message': 'Order ID is required'}), 400
        if not reason:
            return jsonify({'success': False, 'message': 'Reason for cancellation is required'}), 400
            
        from models import Orders, OrderItems, Products, InventoryLogs
        order = db.session.scalars(db.select(Orders).filter_by(id=order_id, user_id=session['user_id'])).first()
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'}), 404
        if order.status not in ['pending', 'processing']:
            return jsonify({'success': False, 'message': f'Order cannot be cancelled in its current status: {order.status}'}), 400
            
        order.status = 'cancelled'
        order.cancelled_at = db.func.now()
        order.cancellation_reason = reason
        
        order_items = db.session.scalars(db.select(OrderItems).filter_by(order_id=order_id)).all()
        for item in order_items:
            product = db.session.scalars(db.select(Products).filter_by(id=item.product_id)).first()
            if product:
                previous_qty = product.stock_quantity or 0
                new_qty = previous_qty + item.quantity
                product.stock_quantity = new_qty
                
                inv_log = InventoryLogs(
                    product_id=item.product_id,
                    previous_quantity=previous_qty,
                    adjustment=item.quantity,
                    new_quantity=new_qty,
                    notes=f"Stock restored for cancelled Order #{order_id}",
                    adjusted_by="system",
                    adjustment_type="cancel",
                    reference_id=str(order_id)
                )
                db.session.add(inv_log)
                
        try:
            from utils.notifications import trigger_all_order_notifications
            trigger_all_order_notifications(order_id, 'cancelled', {'reason': reason})
        except Exception as notify_e:
            logging.info(f"Error triggering cancellation notifications: {str(notify_e)}")
            
        db.session.commit()
        return jsonify({'success': True, 'message': 'Order cancelled successfully'})
    except Exception as e:
        db.session.rollback()
        logging.info(f"Error cancelling order: {str(e)}")
        return jsonify({'success': False, 'message': 'Error cancelling order'}), 500

@checkout_bp.route('/request-return', methods=['POST'])
@login_required
def request_return():
    try:
        order_id = request.form.get('order_id', type=int)
        return_reason = request.form.get('return_reason', '').strip()
        remarks = request.form.get('remarks', '').strip()
        if not order_id:
            return jsonify({'success': False, 'message': 'Order ID is required'}), 400
        if not return_reason:
            return jsonify({'success': False, 'message': 'Return reason is required'}), 400
            
        from models import Orders, OrderReturns, ReturnItems, OrderItems
        order = db.session.scalars(db.select(Orders).filter_by(id=order_id, user_id=session['user_id'])).first()
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'}), 404
        if order.status not in ['delivered', 'refunded', 'partial refunded', 'returned']:
            return jsonify({'success': False, 'message': 'Only delivered or partially returned orders can be returned'}), 400
            
        existing_return = db.session.scalars(db.select(OrderReturns).filter(OrderReturns.order_id == order_id, OrderReturns.status.notin_(['rejected', 'completed']))).first()
        if existing_return:
            return jsonify({'success': False, 'message': 'Return already requested for this order'}), 400
            
        evidence_files = []
        if 'evidence' in request.files:
            files = request.files.getlist('evidence')
            for file in files:
                if file and file.filename:
                    returns_dir = os.path.join(str(current_app.static_folder), 'returns')
                    os.makedirs(returns_dir, exist_ok=True)
                    filename = secure_filename(f"{order_id}_{uuid.uuid4().hex}_{file.filename}")
                    file_path = os.path.join(returns_dir, filename)
                    file.save(file_path)
                    evidence_files.append({'filename': filename, 'type': file.content_type})
                    
        new_return = OrderReturns(
            order_id=order_id,
            user_id=session['user_id'],
            reason=return_reason,
            remarks=remarks,
            evidence_files=evidence_files if evidence_files else None
        )
        db.session.add(new_return)
        db.session.flush()
        return_id = new_return.id

        item_ids = request.form.getlist('item_ids')
        for product_id in item_ids:
            qty = request.form.get(f'qty_{product_id}', type=int)
            if qty and qty > 0:
                oi = db.session.scalars(db.select(OrderItems).filter_by(order_id=order_id, product_id=product_id)).first()
                if not oi:
                    continue
                new_return_item = ReturnItems(
                    return_id=return_id,
                    product_id=product_id,
                    quantity=qty
                )
                db.session.add(new_return_item)

        order.status = 'return_requested'
        try:
            from utils.notifications import trigger_all_order_notifications
            trigger_all_order_notifications(order_id, 'return_requested', {'reason': return_reason, 'remarks': remarks})
        except Exception as e:
            logging.info(f"Error setting up return confirmation email: {str(e)}")
            
        db.session.commit()
        return jsonify({'success': True, 'message': 'Return request submitted successfully'})
    except Exception as e:
        db.session.rollback()
        logging.info(f"Error processing return request: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error processing return request: {str(e)}'}), 500

@checkout_bp.route('/order/<int:order_id>/gst-breakdown')
@login_required
def get_gst_breakdown(order_id):
    try:
        order = db.session.scalars(db.select(Orders).filter_by(id=order_id, user_id=session['user_id'])).first()
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'}), 404
            
        gst_breakdown = calculate_gst_breakdown(order_id)
        return jsonify({'success': True, 'data': gst_breakdown})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@checkout_bp.route('/order/<int:order_id>/return-details')
@login_required
def get_return_details(order_id):
    try:
        order = db.session.scalars(db.select(Orders).filter_by(id=order_id, user_id=session['user_id'])).first()
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'}), 404
            
        # Fetch return details using ORM
        return_details_obj = db.session.scalars(
            db.select(OrderReturns).filter_by(order_id=order_id)
        ).first()
        
        if not return_details_obj:
            return jsonify({'success': False, 'message': 'No return found for this order'}), 404
            
        # Fetch associated items with products
        return_items = db.session.execute(
            db.select(ReturnItems, Products)
            .join(Products, ReturnItems.product_id == Products.id)
            .filter(ReturnItems.return_id == return_details_obj.id)
        ).all()
        
        products_list = []
        for ri, p in return_items:
            products_list.append({
                'product_id': ri.product_id,
                'name': p.name,
                'quantity': ri.quantity
            })
            
        evidence_files = []
        if return_details_obj.evidence_files:
            try:
                # SQLAlchemy handles JSON fields automatically if mapped as JSON,
                # but if it's a string, we might need to load it. In models.py it's mapped as JSON.
                if isinstance(return_details_obj.evidence_files, str):
                    evidence_files = json.loads(return_details_obj.evidence_files)
                else:
                    evidence_files = return_details_obj.evidence_files
            except:
                evidence_files = []
                
        # Status might be an enum, so take the value
        status_value = return_details_obj.status.value if hasattr(return_details_obj.status, 'value') else return_details_obj.status
        
        return jsonify({
            'success': True,
            'return_details': {
                'id': return_details_obj.id,
                'status': status_value,
                'requested_date': return_details_obj.requested_date.isoformat() if return_details_obj.requested_date else None,
                'updated_date': return_details_obj.updated_date.isoformat() if return_details_obj.updated_date else None,
                'reason': return_details_obj.reason,
                'remarks': return_details_obj.remarks,
                'products': products_list,
                'evidence': evidence_files
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@checkout_bp.route('/validate-coupon', methods=['POST'])
def validate_coupon():
    try:
        coupon_code = request.form.get('coupon_code', '').strip().upper()
        guest_id = session.get('guest_id') or request.cookies.get('guest_id') if 'user_id' not in session else None
        user_id = session.get('user_id')
        
        from models import Cart, GuestCart, Products, Coupons
        
        if user_id:
            cart_items = db.session.execute(
                db.select(Cart, Products).join(Products, Cart.product_id == Products.id).filter(Cart.user_id == user_id)
            ).all()
        else:
            cart_items = db.session.execute(
                db.select(GuestCart, Products).join(Products, GuestCart.product_id == Products.id).filter(GuestCart.guest_id == guest_id)
            ).all()
            
        if not cart_items:
            return jsonify({'valid': False, 'message': 'Your cart is empty'})
            
        subtotal = sum(float(p.price or 0) * int(c.quantity) for c, p in cart_items)
        if not coupon_code:
            return jsonify({'valid': False, 'message': 'Please enter a coupon code'})
        
        coupon = db.session.scalars(db.select(Coupons).filter(Coupons.code == coupon_code, Coupons.is_active == 1, Coupons.expiry > db.func.now())).first()
        
        if coupon:
            min_order = float(coupon.min_order or 0)
            disc_val = float(coupon.discount_value or 0)
            disc_type = coupon.discount_type.value if hasattr(coupon.discount_type, 'value') else str(coupon.discount_type)
            if subtotal >= min_order:
                if disc_type == 'percentage':
                    discount_amount = subtotal * (disc_val / 100.0)
                else:
                    discount_amount = disc_val
                discount_amount = min(discount_amount, subtotal)
                session['applied_coupon'] = coupon_code
                return jsonify({'valid': True, 'discount_amount': discount_amount, 'message': 'Coupon applied successfully!'})
            else:
                return jsonify({'valid': False, 'message': f'Minimum order amount of ₹{min_order} required for this coupon'})
        else:
            return jsonify({'valid': False, 'message': 'Invalid or expired coupon code'})
    except Exception as e:
        logging.info(f"Error fetching cart for coupon: {e}")
        return jsonify({'valid': False, 'message': 'Could not validate coupon'}), 500

@checkout_bp.route('/apply-coupon', methods=['POST'])
def apply_coupon():
    return validate_coupon()

@checkout_bp.route('/remove-applied-coupon', methods=['POST'])
def remove_applied_coupon():
    session.pop('applied_coupon', None)
    return jsonify({'success': True, 'message': 'Coupon removed'})

@checkout_bp.route('/download_invoice/<int:order_id>')
@login_required
def download_invoice(order_id):
    try:
        order = db.session.scalars(db.select(Orders).filter_by(id=order_id, user_id=session['user_id'])).first()
        if not order:
            flash('Order not found', 'error')
            return redirect(url_for('pages_bp.home'))
            
        if not order.invoice_number:
            flash('Invoice will be generated once the order is Ready to Dispatch.', 'warning')
            return redirect(url_for('checkout_bp.order_confirmation', order_id=order_id))
            
        invoice_number = order.invoice_number
        
        buffer, error = generate_invoice_pdf(order_id, None, current_app, invoice_number)
        if error:
            current_app.logger.error(f"Invoice error for order {order_id}: {error}")
            flash(f'Error generating invoice: {error}', 'error')
            return redirect(url_for('checkout_bp.order_confirmation', order_id=order_id))
            
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f"invoice_{order_id}.pdf", mimetype='application/pdf')
    except Exception as e:
        logging.info(f"Error in invoice download: {str(e)}")
        current_app.logger.error(f"Invoice exception for order {order_id}: {str(e)}")
        flash('Error generating invoice', 'error')
        return redirect(url_for('checkout_bp.order_confirmation', order_id=order_id))

@checkout_bp.route('/rate-product/<int:order_id>/<int:product_id>', methods=['GET', 'POST'])
@login_required
def rate_product(order_id, product_id):
    try:
        user_id = session['user_id']
        
        # Check if the user ordered this product and the order is delivered
        order_item = db.session.execute(
            db.select(OrderItems)
            .join(Orders, OrderItems.order_id == Orders.id)
            .filter(
                OrderItems.order_id == order_id,
                OrderItems.product_id == product_id,
                Orders.user_id == user_id,
                Orders.status == 'delivered'
            )
        ).first()
        
        if not order_item:
            flash('You cannot review this product', 'error')
            return redirect(url_for('checkout_bp.order_history'))
            
        existing_review_obj = db.session.scalars(
            db.select(ProductReviews)
            .filter_by(user_id=user_id, product_id=product_id, order_id=order_id)
        ).first()
        
        existing_review = None
        if existing_review_obj:
            media_files = []
            if existing_review_obj.media_files:
                try:
                    if isinstance(existing_review_obj.media_files, str):
                        media_files = json.loads(existing_review_obj.media_files)
                    else:
                        media_files = existing_review_obj.media_files
                except:
                    media_files = []
                    
            existing_review = {
                'id': existing_review_obj.id,
                'rating': existing_review_obj.rating,
                'title': existing_review_obj.title,
                'review_text': existing_review_obj.review_text,
                'media_files': existing_review_obj.media_files,
                'parsed_media_files': media_files
            }
            
        product = db.session.scalars(db.select(Products).filter_by(id=product_id)).first()
        
        if request.method == 'POST':
            rating = int(request.form.get('rating', 0))
            title = request.form.get('title', '').strip()
            review_text = request.form.get('review_text', '').strip()
            if rating is None or not (1 <= rating <= 5):
                flash('Please select a valid rating', 'error')
                return redirect(url_for('checkout_bp.rate_product', order_id=order_id, product_id=product_id))
            if not title:
                flash('Please provide a title for your review', 'error')
                return redirect(url_for('checkout_bp.rate_product', order_id=order_id, product_id=product_id))
                
            media_files = []
            if 'media_files' in request.files:
                files = request.files.getlist('media_files')
                for file in files:
                    if file and file.filename:
                        file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
                        if file_ext in ['jpg', 'jpeg', 'png', 'gif']:
                            upload_dir = os.path.join(str(current_app.static_folder), 'uploads', 'reviews', 'images')
                        elif file_ext in ['mp4', 'mov', 'avi', 'mkv']:
                            upload_dir = os.path.join(str(current_app.static_folder), 'uploads', 'reviews', 'videos')
                        else:
                            continue
                        os.makedirs(upload_dir, exist_ok=True)
                        filename = secure_filename(f"{order_id}_{product_id}_{uuid.uuid4().hex}_{file.filename}")
                        file_path = os.path.join(upload_dir, filename)
                        file.save(file_path)
                        relative_path = f"uploads/reviews/{'images' if file_ext in ['jpg', 'jpeg', 'png', 'gif'] else 'videos'}/{filename}"
                        media_files.append({'filename': filename, 'path': relative_path, 'type': file.content_type})
                        
            if existing_review_obj:
                existing_review_obj.rating = rating
                existing_review_obj.title = title
                existing_review_obj.review_text = review_text
                if media_files:
                    existing_review_obj.media_files = media_files
                flash('Your review has been updated', 'success')
            else:
                new_review = ProductReviews(
                    user_id=user_id,
                    product_id=product_id,
                    order_id=order_id,
                    title=title,
                    rating=rating,
                    review_text=review_text,
                    media_files=media_files if media_files else None
                )
                db.session.add(new_review)
                flash('Thank you for your review!', 'success')
                
            db.session.commit()
            return redirect(url_for('checkout_bp.order_history'))
            
        return render_template('rate_product.html', order_id=order_id, product=product, existing_review=existing_review,
                               user_logged_in=True, username=session.get("username"))
    except Exception as e:
        db.session.rollback()
        logging.info(f"Error in rate_product: {str(e)}")
        flash('Error submitting review', 'error')
        return redirect(url_for('checkout_bp.order_history'))

@checkout_bp.route('/submit-review', methods=['POST'])
@login_required
def submit_review():
    product_id = None
    try:
        product_id = request.form.get('product_id', type=int)
        order_id = request.form.get('order_id', type=int)
        rating = request.form.get('rating', type=int)
        title = request.form.get('title', '').strip()
        review_text = request.form.get('review_text', '').strip()
        if rating is None or not (1 <= rating <= 5):
            flash('Please select a valid rating between 1 and 5', 'error')
            return redirect(url_for('shop_bp.detail', product_id=product_id))
        if not title:
            flash('Please provide a title for your review', 'error')
            return redirect(url_for('shop_bp.detail', product_id=product_id))
            
        from models import OrderItems, Orders, ProductReviews
        
        # Check if user ordered this and it's delivered
        has_ordered = db.session.execute(
            db.select(OrderItems)
            .join(Orders, OrderItems.order_id == Orders.id)
            .filter(OrderItems.order_id == order_id, OrderItems.product_id == product_id, Orders.user_id == session['user_id'], Orders.status == 'delivered')
        ).first()
        if not has_ordered:
            flash('You cannot review this product from this order', 'error')
            return redirect(url_for('shop_bp.detail', product_id=product_id))
            
        has_reviewed = db.session.scalars(db.select(ProductReviews).filter_by(user_id=session['user_id'], product_id=product_id, order_id=order_id)).first()
        if has_reviewed:
            flash('You have already reviewed this product from this order', 'error')
            return redirect(url_for('shop_bp.detail', product_id=product_id))

        media_files = []
        if 'media_files' in request.files:
            files = request.files.getlist('media_files')
            if len(files) > 5:
                flash('You can only upload up to 5 files', 'error')
                return redirect(url_for('shop_bp.detail', product_id=product_id))

            MAX_FILE_SIZE = 16 * 1024 * 1024

            for file in files:
                if file and file.filename:
                    file.seek(0, os.SEEK_END)
                    file_length = file.tell()
                    file.seek(0)
                    if file_length > MAX_FILE_SIZE:
                        flash(f'File "{file.filename}" exceeds the 16 MB size limit.', 'error')
                        return redirect(url_for('shop_bp.detail', product_id=product_id))

                    file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''

                    if file_ext in ['jpg', 'jpeg', 'png', 'gif']:
                        upload_dir = os.path.join(str(current_app.static_folder), 'uploads', 'reviews', 'images')
                    elif file_ext in ['mp4', 'mov', 'avi', 'mkv']:
                        upload_dir = os.path.join(str(current_app.static_folder), 'uploads', 'reviews', 'videos')
                    else:
                        continue

                    os.makedirs(upload_dir, exist_ok=True)

                    safe_fname = secure_filename(file.filename)
                    filename = f"{order_id}_{product_id}_{uuid.uuid4().hex}_{safe_fname}"
                    file_path = os.path.join(upload_dir, filename)
                    file.save(file_path)

                    relative_path = f"uploads/reviews/{'images' if file_ext in ['jpg', 'jpeg', 'png', 'gif'] else 'videos'}/{filename}"
                    media_files.append({'filename': filename, 'path': relative_path, 'type': file.content_type})
                    
        new_review = ProductReviews(
            user_id=session['user_id'],
            product_id=product_id,
            order_id=order_id,
            title=title,
            rating=rating,
            review_text=review_text,
            media_files=media_files if media_files else None
        )
        db.session.add(new_review)
        db.session.commit()
        flash('Thank you for your review!', 'success')
        return redirect(url_for('shop_bp.detail', product_id=product_id))
    except Exception as e:
        db.session.rollback()
        logging.info(f"Error submitting review: {str(e)}")
        flash('Error submitting your review', 'error')
        return redirect(url_for('shop_bp.detail', product_id=product_id))
