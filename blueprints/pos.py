from flask import Blueprint, render_template, request, jsonify, current_app, url_for, send_file, redirect, session
from extensions import db
from models import PosOrders, PosOrderItems, Products, Users
from decimal import Decimal
import json
import os
from pos_invoice_generator import generate_invoice_pdf
from datetime import datetime
from admin.admin_app import admin_login_required

try:
    from phonepe.sdk.pg.payments.v2.standard_checkout_client import StandardCheckoutClient
    from phonepe.sdk.pg.env import Env
    from phonepe.sdk.pg.payments.v2.models.request.standard_checkout_pay_request import StandardCheckoutPayRequest
    from phonepe.sdk.pg.common.models.request.meta_info import MetaInfo
    from phonepe.sdk.pg.payments.v2.models.request.prefill_user_login_details import PrefillUserLoginDetails
    PHONEPE_AVAILABLE = True
except ImportError:
    PHONEPE_AVAILABLE = False

pos_bp = Blueprint('pos_bp', __name__, url_prefix='/pos')

@pos_bp.route('/', methods=['GET'])
@admin_login_required
def pos_terminal():
    """Render the POS Terminal UI"""
    return render_template('pos_terminal.html')

@pos_bp.route('/api/customer/<mobile>', methods=['GET'])
@admin_login_required
def get_customer(mobile):
    """Lookup customer by mobile number"""
    try:
        # Check if query is an email
        if '@' in mobile:
            user = db.session.scalars(db.select(Users).filter_by(email=mobile).order_by(Users.id.desc())).first()
            if user:
                return jsonify({'success': True, 'customer': {'name': user.username, 'email': user.email, 'mobile': user.mobile_number}})
            pos_order = db.session.scalars(db.select(PosOrders).filter_by(customer_email=mobile).order_by(PosOrders.id.desc())).first()
            if pos_order:
                return jsonify({'success': True, 'customer': {'name': pos_order.customer_name, 'email': pos_order.customer_email, 'mobile': pos_order.customer_mobile}})
        else:
            # Query is a mobile number
            user = db.session.scalars(db.select(Users).filter(Users.mobile_number.like(f"%{mobile}%")).order_by(Users.id.desc())).first()
            if user:
                return jsonify({'success': True, 'customer': {'name': user.username, 'email': user.email, 'mobile': user.mobile_number}})
            pos_order = db.session.scalars(db.select(PosOrders).filter(PosOrders.customer_mobile.like(f"%{mobile}%")).order_by(PosOrders.id.desc())).first()
            if pos_order:
                return jsonify({'success': True, 'customer': {'name': pos_order.customer_name, 'email': pos_order.customer_email, 'mobile': pos_order.customer_mobile}})
            
        return jsonify({'success': False})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@pos_bp.route('/api/product/<sku>', methods=['GET'])
@admin_login_required
def get_product(sku):
    """Fetch product by SKU or Barcode"""
    try:
        product = db.session.scalars(db.select(Products).filter_by(sku=sku)).first()
        
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'}), 404
            
        if product.stock_quantity <= 0:
            return jsonify({'success': False, 'message': 'Product out of stock'}), 400
            
        prod_data = {
            'id': product.id,
            'product_name': product.name,
            'sku': product.sku,
            'price': float(product.price or 0),
            'stock_quantity': product.stock_quantity,
            'gst_rate': float(product.gst_rate or 0),
            'hsn_code': product.hsn_code,
            'final_price': float(product.price or 0)
        }
        
        return jsonify({'success': True, 'product': prod_data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500



@pos_bp.route('/phonepe_redirect/<int:order_id>', methods=['GET', 'POST'])
@admin_login_required
def phonepe_redirect(order_id):
    """Handle the redirect back from PhonePe after POS online payment."""
    if not PHONEPE_AVAILABLE:
        return redirect(url_for('pos_bp.pos_terminal'))
        
    phonepe_client = getattr(current_app, 'phonepe_client', None)
    if not phonepe_client:
        return redirect(url_for('pos_bp.pos_terminal'))
        
    merchant_order_id = f"POS_OR_{order_id}"
    
    try:
        status_res = phonepe_client.get_order_status(merchant_order_id)
        
        order = db.session.scalars(db.select(PosOrders).filter_by(id=order_id)).first()
        if not order:
            return redirect(url_for('pos_bp.pos_terminal'))
            
        if status_res.state == 'COMPLETED':
            # 1. Update order status
            order.status = 'completed'
            
            # 2. Deduct Inventory now
            items = db.session.scalars(db.select(PosOrderItems).filter_by(order_id=order_id)).all()
            for item in items:
                product = db.session.scalars(db.select(Products).filter_by(id=item.product_id)).first()
                if product:
                    product.stock_quantity = product.stock_quantity - item.quantity
                
            db.session.commit()
            
            # 3. Redirect back to POS terminal with success flag
            return redirect(url_for('pos_bp.pos_terminal', success_order_id=order_id))
            
        else:
            # Payment failed or cancelled
            order.status = 'cancelled'
            db.session.commit()
            return redirect(url_for('pos_bp.pos_terminal', restore_cart=order_id))
            
    except Exception as e:
        db.session.rollback()
        import traceback; traceback.print_exc()
        return redirect(url_for('pos_bp.pos_terminal', restore_cart=order_id))

@pos_bp.route('/api/restore_cart/<int:order_id>', methods=['GET'])
@admin_login_required
def restore_cart(order_id):
    """Fetch items of a cancelled order to restore the cart on the frontend."""
    try:
        raw_items = db.session.scalars(db.select(PosOrderItems).filter_by(order_id=order_id)).all()
        
        items = []
        for item in raw_items:
            items.append({
                'product_id': item.product_id,
                'quantity': item.quantity,
                'price': float(item.price) if item.price is not None else 0.0,
                'product_name': item.product_name,
                'gst_rate': float(item.gst_rate) if item.gst_rate is not None else 0.0
            })
            
        order = db.session.scalars(db.select(PosOrders).filter_by(id=order_id)).first()
        customer = {}
        if order:
            customer = {
                'customer_name': order.customer_name,
                'customer_mobile': order.customer_mobile,
                'customer_email': order.customer_email
            }
        
        return jsonify({
            'success': True,
            'items': items,
            'customer': customer
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@pos_bp.route('/api/checkout', methods=['POST'])
@admin_login_required
def checkout():
    """Handle POS checkout, inventory deduction, and invoice generation"""
    data = request.json
    cart = data.get('cart', [])
    customer = data.get('customer', {})
    payment = data.get('payment', {})
    options = data.get('options', {})
    
    if not cart:
        return jsonify({'success': False, 'message': 'Cart is empty'}), 400
        
    try:
        # 1. Calculate Totals and Validate Inventory
        subtotal = Decimal('0.00')
        tot_discount = Decimal(str(data.get('discount_amount') or 0))
        tot_taxable = Decimal('0.00')
        tot_cgst = Decimal('0.00')
        tot_sgst = Decimal('0.00')
        tot_igst = Decimal('0.00')
        
        validated_items = []
        for item in cart:
            prod = db.session.scalars(db.select(Products).filter_by(id=item['product_id'])).first()
            if not prod:
                return jsonify({'success': False, 'message': f"Product ID {item['product_id']} not found"}), 400
            
            qty = int(item['quantity'])
            if prod.stock_quantity < qty:
                return jsonify({'success': False, 'message': f"Insufficient stock for {item['product_name']}"}), 400
                
            price = Decimal(str(item['price']))
            line_total = price * qty
            subtotal += line_total
            
            rate = Decimal(str(prod.gst_rate or 18))
            item_discount = Decimal('0.00')
            taxable = line_total / (1 + (rate / 100))
            gst = line_total - taxable
            
            cgst = sgst = gst / 2
            
            tot_taxable += taxable
            tot_cgst += cgst
            tot_sgst += sgst
            
            validated_items.append({
                'product': prod,
                'product_id': prod.id,
                'quantity': qty,
                'price': float(price),
                'product_name': item['product_name'],
                'gst_rate': float(rate),
                'cgst_amount': float(cgst),
                'sgst_amount': float(sgst),
                'igst_amount': float(0),
                'taxable_value': float(taxable),
                'hsn_code': prod.hsn_code
            })
            
        grand_total = subtotal - tot_discount
        tot_gst = tot_cgst + tot_sgst + tot_igst
        
        # 2. Update or Insert pos_orders
        payment_method = payment.get('method', 'CASH')
        split_payments_json = json.dumps(payment.get('splits', [])) if payment_method == 'SPLIT' else None
        
        initial_status = 'pending' if payment_method == 'ONLINE' else 'completed'
        user_name = session.get('admin_username')
        
        existing_order_id = data.get('order_id')
        order = None
        if existing_order_id:
            order = db.session.scalars(db.select(PosOrders).filter_by(id=existing_order_id)).first()
            if order and order.status in ('pending', 'cancelled'):
                # Delete old items
                db.session.execute(db.delete(PosOrderItems).where(PosOrderItems.order_id == existing_order_id))
                
                # Update order
                order.order_date = db.func.now()
                order.total_amount = float(grand_total)
                order.payment_method = payment_method
                order.split_payments = split_payments_json
                order.customer_name = customer.get('name')
                order.customer_mobile = customer.get('mobile')
                order.customer_email = customer.get('email')
                order.discount_amount = float(tot_discount)
                order.status = initial_status
                order.subtotal = float(subtotal)
                order.order_dateonly = db.func.current_date()
                order.taxable_amount = float(tot_taxable)
                order.cgst_amount = float(tot_cgst)
                order.sgst_amount = float(tot_sgst)
                order.igst_amount = float(tot_igst)
                order.total_gst = float(tot_gst)
                order.user_name = user_name
            else:
                existing_order_id = None
                
        if not existing_order_id:
            order = PosOrders(
                order_date=db.func.now(),
                total_amount=float(grand_total),
                payment_method=payment_method,
                split_payments=split_payments_json,
                customer_name=customer.get('name'),
                customer_mobile=customer.get('mobile'),
                customer_email=customer.get('email'),
                discount_amount=float(tot_discount),
                status=initial_status,
                subtotal=float(subtotal),
                order_dateonly=db.func.current_date(),
                taxable_amount=float(tot_taxable),
                cgst_amount=float(tot_cgst),
                sgst_amount=float(tot_sgst),
                igst_amount=float(tot_igst),
                total_gst=float(tot_gst),
                sales_channel='POS',
                user_name=user_name
            )
            db.session.add(order)
            db.session.flush() # Get ID
            
        order_id = order.id
        
        # 3. Insert Items
        for item in validated_items:
            order_item = PosOrderItems(
                order_id=order_id,
                product_id=item['product_id'],
                quantity=item['quantity'],
                price=item['price'],
                product_name=item['product_name'],
                gst_rate=item['gst_rate'],
                cgst_amount=item['cgst_amount'],
                sgst_amount=item['sgst_amount'],
                igst_amount=item['igst_amount'],
                taxable_value=item['taxable_value'],
                hsn_code=item['hsn_code']
            )
            db.session.add(order_item)
            
            # Deduct Inventory ONLY IF NOT ONLINE (ONLINE deducts after successful payment)
            if payment_method != 'ONLINE':
                item['product'].stock_quantity = item['product'].stock_quantity - item['quantity']
            
        db.session.commit()
        
        phonepe_url = None
        invoice_url = None
        
        if payment_method == 'ONLINE' and PHONEPE_AVAILABLE:
            phonepe_client = getattr(current_app, 'phonepe_client', None)
            if phonepe_client:
                merchant_order_id = f"POS_OR_{order_id}"
                amount_paisa = int(float(grand_total) * 100)
                meta_info = MetaInfo(
                    udf1=customer.get('name', 'POS Customer'),
                    udf2=customer.get('email', '')
                )
                prefill_details = PrefillUserLoginDetails(
                    phone_number=customer.get('mobile', '9999999999')
                )
                pay_request = StandardCheckoutPayRequest.build_request(
                    merchant_order_id=merchant_order_id,
                    amount=amount_paisa,
                    redirect_url=url_for('pos_bp.phonepe_redirect', order_id=order_id, _external=True),
                    meta_info=meta_info,
                    prefill_user_login_details=prefill_details,
                    message=f"POS Payment - Aanyaas",
                    expire_after=3600,
                    disable_payment_retry=False
                )
                pay_response = phonepe_client.pay(pay_request)
                phonepe_url = pay_response.redirect_url
        else:
            # 4. Generate Invoice PDF ONLY IF NOT ONLINE
            buffer, err = generate_invoice_pdf(order_id, None, current_app)
            if err:
                print(f"Error generating POS PDF: {err}")
            else:
                invoice_url = f"/pos/invoice/{order_id}" if options.get('print') else None
                
            # 5. Handle Options (Email/SMS) ONLY IF NOT ONLINE
            if options.get('send_email') and customer.get('email'):
                try:
                    from flask_mail import Message
                    mail = current_app.extensions.get('mail')
                    if mail:
                        msg = Message(
                            f"Invoice for Order #S{order_id}",
                            recipients=[customer['email']]
                        )
                        msg.body = f"Hello {customer.get('name', 'Customer')},\n\nThank you for shopping at Aanyaas. Your invoice is attached."
                        if not err and buffer:
                            msg.attach(f"invoice_{order_id}.pdf", "application/pdf", buffer.getvalue())
                        mail.send(msg)
                except Exception as e:
                    print(f"Error sending email: {e}")
                    
            if options.get('send_sms') and customer.get('mobile'):
                # Placeholder for actual WhatsApp/SMS API integration
                print(f"--> [MOCK] SMS Sent to {customer.get('mobile')}: Your Aanyaas Invoice is ready!")
            
        return jsonify({
            'success': True, 
            'order_id': order_id, 
            'invoice_url': invoice_url,
            'phonepe_url': phonepe_url
        })
        
    except Exception as e:
        db.session.rollback()
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@pos_bp.route('/api/eod-report', methods=['GET'])
@admin_login_required
def eod_report():
    try:
        from sqlalchemy import func
        today = db.func.current_date()
        
        summary = db.session.execute(
            db.select(
                func.count(PosOrders.id).label('total_transactions'),
                func.sum(PosOrders.total_amount).label('gross_sales'),
                func.sum(PosOrders.total_gst).label('total_tax'),
                func.sum(PosOrders.discount_amount).label('total_discounts')
            )
            .filter(func.date(PosOrders.order_date) == today, PosOrders.status != 'Refunded')
        ).first()

        methods = db.session.execute(
            db.select(PosOrders.payment_method, func.sum(PosOrders.total_amount).label('amount'))
            .filter(func.date(PosOrders.order_date) == today, PosOrders.status != 'Refunded')
            .group_by(PosOrders.payment_method)
        ).all()

        cash_total = Decimal('0.00')
        online_total = Decimal('0.00')

        for m in methods:
            if m.payment_method == 'CASH':
                cash_total += Decimal(str(m.amount or 0))
            elif m.payment_method == 'ONLINE':
                online_total += Decimal(str(m.amount or 0))

        splits = db.session.execute(
            db.select(PosOrders.split_payments)
            .filter(func.date(PosOrders.order_date) == today, PosOrders.status != 'Refunded', PosOrders.payment_method == 'SPLIT')
        ).scalars().all()
        
        for split in splits:
            if split:
                arr = json.loads(split)
                for p in arr:
                    amt = Decimal(str(p.get('amount', 0)))
                    meth = p.get('method', '').upper()
                    if meth == 'CASH': cash_total += amt
                    elif meth == 'ONLINE': online_total += amt
        
        return jsonify({
            'success': True,
            'summary': {
                'transactions': summary.total_transactions or 0,
                'gross_sales': float(summary.gross_sales or 0),
                'total_tax': float(summary.total_tax or 0),
                'total_discounts': float(summary.total_discounts or 0)
            },
            'breakdown': {
                'CASH': float(cash_total),
                'ONLINE': float(online_total)
            }
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)})

@pos_bp.route('/api/transactions', methods=['GET'])
@admin_login_required
def get_transactions():
    try:
        from sqlalchemy import func
        today = db.func.current_date()
        
        orders = db.session.scalars(
            db.select(PosOrders)
            .filter(func.date(PosOrders.order_date) == today)
            .order_by(PosOrders.id.desc())
            .limit(50)
        ).all()
        
        result = []
        for o in orders:
            item = {
                'id': o.id,
                'total_amount': float(o.total_amount or 0),
                'payment_method': o.payment_method,
                'status': o.status,
                'time': o.order_date.strftime('%I:%M %p') if o.order_date else '',
                'date': o.order_date.strftime('%Y-%m-%d') if o.order_date else ''
            }
            result.append(item)
            
        return jsonify({'success': True, 'transactions': result})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)})

@pos_bp.route('/api/refund/<int:order_id>', methods=['POST'])
@admin_login_required
def process_refund(order_id):
    try:
        order = db.session.scalars(db.select(PosOrders).filter_by(id=order_id)).first()
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'})
        if order.status == 'Refunded':
            return jsonify({'success': False, 'message': 'Already refunded'})
            
        items = db.session.scalars(db.select(PosOrderItems).filter_by(order_id=order_id)).all()
        
        for item in items:
            product = db.session.scalars(db.select(Products).filter_by(id=item.product_id)).first()
            if product:
                product.stock_quantity = product.stock_quantity + item.quantity
            
        order.status = 'Refunded'
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@pos_bp.route('/invoice/<int:order_id>')
@admin_login_required
def print_invoice(order_id):
    """View generated POS invoice"""
    try:
        # Use existing POS invoice generator
        pdf_buffer, error = generate_invoice_pdf(order_id, None, current_app)
        if error:
            return f"Error generating invoice: {error}", 500
            
        return send_file(
            pdf_buffer,
            as_attachment=False, # Show in browser
            download_name=f'invoice_{order_id}.pdf',
            mimetype='application/pdf'
        )
    except Exception as e:
        return f"System error: {str(e)}", 500
