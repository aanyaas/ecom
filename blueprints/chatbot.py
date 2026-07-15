from flask import Blueprint, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import re
from extensions import db
from models import Products, Orders

chatbot_bp = Blueprint('chatbot_bp', __name__)

# Basic rate limiting for the chat API
chat_limiter = Limiter(key_func=get_remote_address)

@chatbot_bp.route('/api/chat', methods=['POST'])
@chat_limiter.limit("20 per minute")
def chat_api():
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({'error': 'Message is required'}), 400

    user_message = data['message'].lower().strip()
    
    # 1. Check for Order Tracking Intent
    track_match = re.search(r'(?:track|status).*?order\s*#?\s*(\d+)', user_message)
    if track_match:
        order_id = track_match.group(1)
        order = db.session.get(Orders, order_id)
        if order:
            return jsonify({
                'status': 'success',
                'reply': f"Order #{order_id} is currently marked as '{order.status.upper()}'. It was placed on {order.order_date.strftime('%b %d, %Y')}."
            })
        else:
            return jsonify({
                'status': 'success',
                'reply': f"I couldn't find order #{order_id} in our system. Please double-check the order number."
            })

    # 2. Check for Product Search Intent
    product_match = re.search(r'(?:do you have|looking for|search for|find)\s+(.*)', user_message)
    if product_match:
        keyword = product_match.group(1).replace('?', '').strip()
        # Ensure keyword is not too short
        if len(keyword) > 2:
            search_pattern = f"%{keyword}%"
            product = db.session.scalars(
                db.select(Products).where(
                    db.and_(
                        Products.is_active == 1,
                        db.or_(
                            Products.name.ilike(search_pattern),
                            Products.category.ilike(search_pattern)
                        )
                    )
                ).limit(1)
            ).first()

            if product:
                in_stock = "in stock" if product.stock > 0 else "out of stock"
                return jsonify({
                    'status': 'success',
                    'reply': f"Yes! We found '{product.name}' in the {product.category} category. It is currently {in_stock} for ₹{product.price}."
                })
            else:
                return jsonify({
                    'status': 'success',
                    'reply': f"Sorry, I couldn't find anything matching '{keyword}'. Try checking our Shop page for our full catalog."
                })

    # 3. Fallback Keyword NLP
    response_text = "I'm a simple support bot! You can ask me about our shipping policy, returns, or track an order (e.g. 'track order 123')."
    
    if any(word in user_message for word in ['ship', 'shipping', 'delivery']):
        response_text = "We offer standard shipping (5-7 business days) and expedited shipping (2-3 business days). Shipping is free on orders over ₹1000!"
    elif any(word in user_message for word in ['return', 'refund', 'exchange']):
        response_text = "We have a 30-day hassle-free return policy. As long as the item is in original condition, you can return it for a full refund."
    elif any(word in user_message for word in ['contact', 'support', 'help', 'email']):
        response_text = "You can reach our human support team at support@aanyaas.com or call us at 1-800-AANYAAS."
    elif any(word in user_message for word in ['hi', 'hello', 'hey']):
        response_text = "Hello there! How can I assist you with your shopping today?"
    elif any(word in user_message for word in ['payment', 'pay', 'card']):
        response_text = "We accept all major credit cards, UPI, and net banking via our secure PhonePe gateway."

    return jsonify({
        'status': 'success',
        'reply': response_text
    })
