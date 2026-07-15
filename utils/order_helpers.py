from utils.notifications import trigger_all_order_notifications
import logging

def finalize_successful_order(order_id, merchant_order_id=None):
    """
    Idempotently finalize an order: update status, reduce stock, clear cart, notify.
    """
    try:
        from extensions import db
        from sqlalchemy import text
        
        # 1. Check if already paid
        order_row = db.session.execute(text("SELECT payment_status, user_id FROM orders WHERE id = :id"), {'id': order_id}).fetchone()
        
        if not order_row:
            return False

        if order_row.payment_status == 'paid':
            # Already finalized
            return True

        # 2. Update order status
        if merchant_order_id:
            db.session.execute(text("UPDATE orders SET status = 'processing', payment_status = 'paid', merchant_order_id = :mid WHERE id = :id"), {'mid': merchant_order_id, 'id': order_id})
        else:
            db.session.execute(text("UPDATE orders SET status = 'processing', payment_status = 'paid' WHERE id = :id"), {'id': order_id})

        # 3. Decrement stock
        items = db.session.execute(text("SELECT product_id, quantity FROM order_items WHERE order_id = :id"), {'id': order_id}).fetchall()
        for item in items:
            product_row = db.session.execute(text("SELECT stock_quantity FROM products WHERE id = :id FOR UPDATE"), {'id': item.product_id}).fetchone()
            previous_qty = product_row.stock_quantity if product_row else 0
            new_qty = max(0, previous_qty - item.quantity)
            
            db.session.execute(text("UPDATE products SET stock_quantity = :qty WHERE id = :id"), {'qty': new_qty, 'id': item.product_id})
            
            # Log inventory change
            db.session.execute(text("""
                INSERT INTO inventory_logs
                (product_id, previous_quantity, adjustment, new_quantity,
                 notes, adjusted_by, adjustment_type, reference_id)
                VALUES (:pid, :prev, :adj, :new_qty, :notes, :by, :type, :ref)
            """), {
                'pid': item.product_id, 'prev': previous_qty, 'adj': -item.quantity, 'new_qty': new_qty,
                'notes': f"Stock decremented for paid Online Order #{order_id}", 'by': "system", 'type': "order", 'ref': order_id
            })
        
        # 4. Clear user cart
        user_id = order_row.user_id
        if user_id:
            db.session.execute(text("DELETE FROM cart WHERE user_id = :uid"), {'uid': user_id})
            
        db.session.commit()

        # 5. Trigger notifications (safely outside transaction)
        try:
            trigger_all_order_notifications(order_id, 'placed')
        except Exception as e:
            logging.error(f"Notification error for order {order_id}: {e}")

        return True
    except Exception as e:
        from extensions import db
        db.session.rollback()
        logging.error(f"Error finalizing order {order_id}: {e}")
        return False

def cancel_failed_order(order_id, reason="Payment Failed"):
    """
    Cancel an order, refunding loyalty points and gift cards.
    """
    try:
        from extensions import db
        from sqlalchemy import text
        
        # 1. Check if already cancelled
        order_row = db.session.execute(text("SELECT status, payment_status, user_id, loyalty_points_used, gift_card_id, gift_card_discount FROM orders WHERE id = :id"), {'id': order_id}).fetchone()
        
        if not order_row:
            return False

        if order_row.status == 'cancelled':
            return True

        # 2. Mark as cancelled
        db.session.execute(text("UPDATE orders SET status = 'cancelled', payment_status = 'failed', cancellation_reason = :reason, cancelled_at = NOW() WHERE id = :id"), {'reason': reason, 'id': order_id})

        # 3. Refund Loyalty Points
        user_id = order_row.user_id
        loyalty_used = order_row.loyalty_points_used
        if user_id and loyalty_used and loyalty_used > 0:
            # Check if we already refunded for this order
            already_refunded = db.session.execute(text("SELECT id FROM loyalty_ledger WHERE order_id = :id AND transaction_type = 'refunded'"), {'id': order_id}).fetchone()
            if not already_refunded:
                db.session.execute(text("""
                    INSERT INTO loyalty_ledger (user_id, points, transaction_type, order_id)
                    VALUES (:uid, :pts, 'refunded', :oid)
                """), {'uid': user_id, 'pts': loyalty_used, 'oid': order_id})

        # 4. Refund Gift Cards
        gc_id = order_row.gift_card_id
        gc_discount = order_row.gift_card_discount
        if gc_id and gc_discount and gc_discount > 0:
            # Re-add balance to gift card
            db.session.execute(text("UPDATE gift_cards SET current_balance = current_balance + :discount WHERE id = :id"), {'discount': gc_discount, 'id': gc_id})
            # Create a reverse transaction record
            db.session.execute(text("""
                INSERT INTO gift_card_transactions (gift_card_id, order_id, amount_used)
                VALUES (:gcid, :oid, :amount)
            """), {'gcid': gc_id, 'oid': order_id, 'amount': -gc_discount})

        db.session.commit()
        return True
    except Exception as e:
        from extensions import db
        db.session.rollback()
        logging.error(f"Error cancelling order {order_id}: {e}")
        return False
