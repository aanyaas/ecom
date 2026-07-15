import string
import random
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify
from admin.admin_app import admin_bp, admin_login_required
from extensions import db
from models import GiftCards

def generate_gift_card_code(length=12):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

@admin_bp.route('/gift-cards')
@admin_login_required
def admin_gift_cards():
    try:
        gift_cards = db.session.scalars(db.select(GiftCards).order_by(GiftCards.created_at.desc())).all()
        return render_template('admin/gift_cards.html', gift_cards=gift_cards)
    except Exception as e:
        print(f"Error fetching gift cards: {e}")
        flash('Error fetching gift cards', 'danger')
        return render_template('admin/gift_cards.html', gift_cards=[])

@admin_bp.route('/gift-cards/generate', methods=['POST'])
@admin_login_required
def admin_generate_gift_card():
    amount = request.form.get('amount', type=float)
    expiry = request.form.get('expiry_date')
    
    if not amount or amount <= 0:
        flash('Valid amount is required', 'danger')
        return redirect(url_for('admin_bp.admin_gift_cards'))
        
    code = request.form.get('custom_code', '').strip().upper()
    if not code:
        code = generate_gift_card_code()
        
    expiry_date = expiry if expiry else None

    try:
        new_card = GiftCards(
            code=code,
            initial_balance=amount,
            current_balance=amount,
            expiry_date=expiry_date
        )
        db.session.add(new_card)
        db.session.commit()
        flash(f'Gift Card {code} generated successfully with balance Rs. {amount}', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error generating gift card: {e}")
        flash('Error generating gift card. Code might already exist.', 'danger')
            
    return redirect(url_for('admin_bp.admin_gift_cards'))

@admin_bp.route('/gift-cards/<int:card_id>/toggle', methods=['POST'])
@admin_login_required
def admin_toggle_gift_card(card_id):
    try:
        card = db.session.scalars(db.select(GiftCards).filter_by(id=card_id)).first()
        if card:
            card.is_active = not card.is_active
            db.session.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Card not found'}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
