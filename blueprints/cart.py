import traceback
import uuid
from flask import Blueprint, request, session, redirect, url_for, jsonify
from extensions import db
from models import Cart, GuestCart, Products, Coupons
from utils.session_helpers import get_guest_or_user_cart_count
from datetime import datetime, UTC

cart_bp = Blueprint('cart_bp', __name__)

@cart_bp.route("/cart")
def cart():
    referrer = request.referrer or url_for('shop_bp.shop')
    if not referrer or '/cart' in referrer or '/checkout' in referrer:
        referrer = url_for('shop_bp.shop')
    
    if '?' in referrer:
        if 'open_cart=1' not in referrer:
            referrer += '&open_cart=1'
    else:
        referrer += '?open_cart=1'
        
    return redirect(referrer)

@cart_bp.route('/api/cart-details')
def api_cart_details():
    try:
        guest_id = session.get('guest_id') or request.cookies.get('guest_id') if 'user_id' not in session else None
        user_id = session.get('user_id')
        
        items = []
        if user_id:
            items_query = db.session.query(Cart, Products).join(Products, Cart.product_id == Products.id).filter(Cart.user_id == user_id).all()
            for c, p in items_query:
                items.append({
                    'id': p.id,
                    'name': p.name,
                    'price': float(p.price),
                    'mrp': float(p.mrp) if p.mrp else float(p.price),
                    'image': p.image,
                    'quantity': c.quantity,
                    'stock_quantity': p.stock_quantity,
                    'image_url': url_for('static', filename='img/thumbs/' + (p.image or 'default.jpg'))
                })
        elif guest_id:
            items_query = db.session.query(GuestCart, Products).join(Products, GuestCart.product_id == Products.id).filter(GuestCart.guest_id == guest_id).all()
            for gc, p in items_query:
                items.append({
                    'id': p.id,
                    'name': p.name,
                    'price': float(p.price),
                    'mrp': float(p.mrp) if p.mrp else float(p.price),
                    'image': p.image,
                    'quantity': gc.quantity,
                    'stock_quantity': p.stock_quantity,
                    'image_url': url_for('static', filename='img/thumbs/' + (p.image or 'default.jpg'))
                })

        subtotal = sum(item['price'] * item['quantity'] for item in items) # type: ignore
        total_quantity = sum(item['quantity'] for item in items) # type: ignore
        
        applied_coupon_code = session.get('applied_coupon')
        discount = 0.0
        if applied_coupon_code:
            coupon = db.session.scalars(db.select(Coupons).filter_by(code=applied_coupon_code, is_active=1).filter(Coupons.expiry > datetime.now(UTC).replace(tzinfo=None))).first()
            if coupon:
                disc_val = float(coupon.discount_value)
                disc_type = coupon.discount_type.value if hasattr(coupon.discount_type, 'value') else coupon.discount_type
                min_order = float(coupon.min_order) if coupon.min_order else 0.0
                if subtotal >= min_order:
                    if disc_type == 'percentage':
                        discount = subtotal * (disc_val / 100.0)
                    else:
                        discount = disc_val
                    discount = min(discount, subtotal)
                else:
                    session.pop('applied_coupon', None)
                    applied_coupon_code = None
            else:
                session.pop('applied_coupon', None)
                applied_coupon_code = None

        free_shipping_threshold = 500.00
        default_shipping_charge = 99.00
        shipping_charge = 0.0 if subtotal >= free_shipping_threshold or subtotal == 0 else default_shipping_charge
        total = subtotal - discount + shipping_charge
        
        total_mrp = sum(item['mrp'] * item['quantity'] for item in items) # type: ignore
        total_savings = (total_mrp - subtotal) + discount

        return jsonify({
            'success': True,
            'cart_items': items,
            'subtotal': subtotal,
            'discount_amount': discount,
            'shipping_charge': shipping_charge,
            'total': total,
            'total_quantity': total_quantity,
            'coupon_code': applied_coupon_code,
            'free_shipping_threshold': free_shipping_threshold,
            'total_savings': total_savings
        })
    except Exception as e:
        print(f"Error in api_cart_details via ORM: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@cart_bp.route('/add_to_cart/<int:product_id>', methods=['POST'])
def add_to_cart(product_id):
    try:
        quantity = int(request.form.get('quantity', 1))
        if quantity < 1:
            return jsonify({'success': False, 'message': 'Quantity must be at least 1'}), 400

        user_id = session.get('user_id')
        guest_id = None
        if not user_id:
            guest_id = session.get('guest_id')
            if not guest_id:
                guest_id = request.cookies.get('guest_id')
                if not guest_id:
                    guest_id = str(uuid.uuid4())
                    session['guest_id'] = guest_id

        product = db.session.scalars(db.select(Products).filter_by(id=product_id, is_active=1)).first()
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'}), 404

        current_qty_in_cart = 0
        cart_item = None
        if user_id:
            cart_item = db.session.scalars(db.select(Cart).filter_by(user_id=user_id, product_id=product_id)).first()
        else:
            cart_item = db.session.scalars(db.select(GuestCart).filter_by(guest_id=guest_id, product_id=product_id)).first()
            
        if cart_item:
            current_qty_in_cart = cart_item.quantity

        requested_total_qty = current_qty_in_cart + quantity
        stock = product.stock_quantity if product.stock_quantity else 0
        if requested_total_qty > stock:
            if stock == 0:
                return jsonify({'success': False, 'message': 'This item is currently out of stock.'}), 400
            elif current_qty_in_cart > 0:
                return jsonify({'success': False, 'message': f'You already have {current_qty_in_cart} items in your cart. Only {stock} are available in stock.'}), 400
            else:
                return jsonify({'success': False, 'message': f'Only {stock} items are available in stock.'}), 400

        if user_id:
            if cart_item:
                cart_item.quantity += quantity
            else:
                new_item = Cart(user_id=user_id, product_id=product_id, quantity=quantity)
                db.session.add(new_item)
        else:
            if cart_item:
                cart_item.quantity += quantity
            else:
                new_item = GuestCart(guest_id=guest_id, product_id=product_id, quantity=quantity)
                db.session.add(new_item)
                
        db.session.commit()

        cart_count = get_guest_or_user_cart_count()
        return jsonify({'success': True, 'message': 'Product added to cart successfully', 'cart_count': cart_count})
    except Exception as e:
        db.session.rollback()
        print(f"Error in add_to_cart via ORM: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred'}), 500

@cart_bp.route('/update_cart_item', methods=['POST'])
def update_cart_item():
    try:
        product_id = int(request.form.get('product_id') or 0)
        quantity = int(request.form.get('quantity', 1))
        guest_id = session.get('guest_id') or request.cookies.get('guest_id') if 'user_id' not in session else None
        user_id = session.get('user_id')
        if quantity < 1:
            quantity = 1
            
        product = db.session.scalars(db.select(Products).filter_by(id=product_id)).first()
        if not product:
            return jsonify({'success': False, 'error': 'Product not found'}), 404
            
        stock = product.stock_quantity if product.stock_quantity else 0
        if quantity > stock:
            return jsonify({'success': False, 'error': f'Only {stock} items available'}), 400
            
        cart_item = None
        if user_id:
            cart_item = db.session.scalars(db.select(Cart).filter_by(user_id=user_id, product_id=product_id)).first()
        else:
            cart_item = db.session.scalars(db.select(GuestCart).filter_by(guest_id=guest_id, product_id=product_id)).first()
            
        if not cart_item:
            return jsonify({'success': False, 'error': 'Item not in cart'}), 404
            
        cart_item.quantity = quantity
        db.session.commit()
        
        # Recalculate totals
        subtotal = 0.0
        total_quantity = 0
        unique_items = 0
        
        if user_id:
            items_query = db.session.query(Cart, Products).join(Products, Cart.product_id == Products.id).filter(Cart.user_id == user_id).all()
            unique_items = len(items_query)
            for c, p in items_query:
                subtotal += float(p.price) * c.quantity
                total_quantity += c.quantity
        else:
            items_query = db.session.query(GuestCart, Products).join(Products, GuestCart.product_id == Products.id).filter(GuestCart.guest_id == guest_id).all()
            unique_items = len(items_query)
            for gc, p in items_query:
                subtotal += float(p.price) * gc.quantity
                total_quantity += gc.quantity
                
        return jsonify({'success': True, 'subtotal': subtotal, 'total_quantity': total_quantity, 'unique_items': unique_items})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@cart_bp.route('/remove-from-cart/<int:product_id>', methods=['POST'])
def remove_from_cart(product_id):
    try:
        guest_id = session.get('guest_id') or request.cookies.get('guest_id') if 'user_id' not in session else None
        user_id = session.get('user_id')
        
        if user_id:
            cart_item = db.session.scalars(db.select(Cart).filter_by(user_id=user_id, product_id=product_id)).first()
            if cart_item:
                db.session.delete(cart_item)
        else:
            cart_item = db.session.scalars(db.select(GuestCart).filter_by(guest_id=guest_id, product_id=product_id)).first()
            if cart_item:
                db.session.delete(cart_item)
                
        db.session.commit()

        # Recalculate totals
        subtotal = 0.0
        total_quantity = 0
        unique_items = 0
        
        if user_id:
            items_query = db.session.query(Cart, Products).join(Products, Cart.product_id == Products.id).filter(Cart.user_id == user_id).all()
            unique_items = len(items_query)
            for c, p in items_query:
                subtotal += float(p.price) * c.quantity
                total_quantity += c.quantity
        else:
            items_query = db.session.query(GuestCart, Products).join(Products, GuestCart.product_id == Products.id).filter(GuestCart.guest_id == guest_id).all()
            unique_items = len(items_query)
            for gc, p in items_query:
                subtotal += float(p.price) * gc.quantity
                total_quantity += gc.quantity

        cart_count = get_guest_or_user_cart_count()

        return jsonify({
            'success': True,
            'message': 'Product removed from cart successfully',
            'cart_count': cart_count,
            'subtotal': subtotal,
            'total_quantity': total_quantity,
            'unique_items': unique_items
        })
    except Exception as e:
        db.session.rollback()
        print(f"Error removing from cart via ORM: {str(e)}")
        return jsonify({'success': False, 'message': 'Error removing item'}), 500

@cart_bp.route('/get_cart_count')
@cart_bp.route('/getcartcount')
def get_cart_count():
    try:
        if 'user_id' in session:
            user_id = session['user_id']
            items_query = db.session.query(Cart, Products).join(Products, Cart.product_id == Products.id).filter(Cart.user_id == user_id).all()
            unique_items = len(items_query)
            total_quantity = sum(c.quantity for c, p in items_query)
            subtotal = sum(float(p.price) * c.quantity for c, p in items_query)
            
            return jsonify({
                'unique_items': unique_items,
                'total_quantity': total_quantity,
                'subtotal': subtotal
            })
            
        elif 'guest_id' in session or request.cookies.get('guest_id'):
            guest_id = session.get('guest_id') or request.cookies.get('guest_id')
            items_query = db.session.query(GuestCart, Products).join(Products, GuestCart.product_id == Products.id).filter(GuestCart.guest_id == guest_id).all()
            unique_items = len(items_query)
            total_quantity = sum(gc.quantity for gc, p in items_query)
            subtotal = sum(float(p.price) * gc.quantity for gc, p in items_query)
            
            return jsonify({
                'unique_items': unique_items,
                'total_quantity': total_quantity,
                'subtotal': subtotal
            })
            
        else:
            return jsonify({
                'unique_items': 0,
                'total_quantity': 0,
                'subtotal': 0.0
            })
    except Exception as e:
        print(f"Error getting cart count via ORM: {str(e)}")
        return jsonify({'unique_items': 0, 'total_quantity': 0, 'subtotal': 0.0})
