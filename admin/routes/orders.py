import logging
import json
import uuid

from flask import render_template, request, redirect, url_for, flash, jsonify, current_app, send_file
from admin.admin_app import (
    admin_bp, admin_login_required
)
from invoice_generator import generate_invoice_pdf, generate_invoice_number_new
from utils.notifications import trigger_all_order_notifications
from extensions import db
from sqlalchemy import text  # type: ignore

try:
    from phonepe.sdk.pg.common.models.request.refund_request import RefundRequest
    PHONEPE_REFUND_AVAILABLE = True
except ImportError:
    RefundRequest = None
    PHONEPE_REFUND_AVAILABLE = False

@admin_bp.route('/order/<int:order_id>/return/<int:return_id>/update', methods=['POST'])
@admin_login_required
def update_return_status(return_id, order_id):
    try:
        new_status = request.form.get('status')
        rejection_reason = request.form.get('rejection_reason', '')
        refund_amount = request.form.get('refund_amount', 0)
        completion_notes = request.form.get('completion_notes', '')

        try:
            # Get current return status
            current_status_res = db.session.execute(text("SELECT status FROM order_returns WHERE id = :id"), {'id': return_id}).fetchone()
            if not current_status_res:
                flash('Return request not found', 'error')
                return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))
            current_status = current_status_res.status

            # Validate status transition
            valid_transitions = {
                'requested': ['approved', 'rejected'],
                'approved': ['processing'],
                'processing': ['completed'],
                'rejected': [],
                'completed': []
            }

            if new_status not in valid_transitions.get(current_status, []):
                flash('Invalid status transition', 'error')
                return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

            if new_status == 'rejected':
                db.session.execute(text("""
                    UPDATE order_returns
                    SET status = :status, remarks = CONCAT(COALESCE(remarks, ''), ' Rejection Reason: ', :reason), updated_date = NOW()
                    WHERE id = :id
                """), {'status': new_status, 'reason': rejection_reason, 'id': return_id})
                
                # Revert order status
                total_ordered = db.session.execute(text("SELECT SUM(quantity) as total_ordered FROM order_items WHERE order_id = :id"), {'id': order_id}).scalar() or 0
                
                total_returned = db.session.execute(text("""
                    SELECT SUM(ri.quantity) as total_returned
                    FROM return_items ri
                    JOIN order_returns orr ON ri.return_id = orr.id
                    WHERE orr.order_id = :id AND orr.status = 'completed'
                """), {'id': order_id}).scalar() or 0
                
                if total_returned == 0:
                    final_order_status = 'delivered'
                elif total_returned >= total_ordered:
                    final_order_status = 'refunded'
                else:
                    final_order_status = 'partial refunded'
                    
                db.session.execute(text("UPDATE orders SET status = :status WHERE id = :id"), {'status': final_order_status, 'id': order_id})
            elif new_status == 'completed':
                db.session.execute(text("""
                    UPDATE order_returns
                    SET status = :status, remarks = CONCAT(COALESCE(remarks, ''), ' Completion Notes: ', :notes), updated_date = NOW()
                    WHERE id = :id
                """), {'status': new_status, 'notes': completion_notes, 'id': return_id})

                db.session.execute(text("""
                    UPDATE orders
                    SET return_amount = COALESCE(return_amount, 0) + :amount, returned_at = NOW()
                    WHERE id = :id
                """), {'amount': float(refund_amount), 'id': order_id})

                total_ordered = db.session.execute(text("SELECT SUM(quantity) as total_ordered FROM order_items WHERE order_id = :id"), {'id': order_id}).scalar() or 0

                total_returned = db.session.execute(text("""
                    SELECT SUM(ri.quantity) as total_returned
                    FROM return_items ri
                    JOIN order_returns orr ON ri.return_id = orr.id
                    WHERE orr.order_id = :id AND orr.status = 'completed'
                """), {'id': order_id}).scalar() or 0

                if total_returned >= total_ordered:
                    final_order_status = 'refunded'
                else:
                    final_order_status = 'partial refunded'

                db.session.execute(text("UPDATE orders SET status = :status WHERE id = :id"), {'status': final_order_status, 'id': order_id})

                try:
                    r_items = db.session.execute(text("""
                        SELECT ri.id as ri_id, ri.quantity as return_qty, oi.quantity as order_qty,
                               oi.taxable_value, oi.cgst_amount, oi.sgst_amount, oi.igst_amount
                        FROM return_items ri
                        JOIN order_items oi ON ri.product_id = oi.product_id AND oi.order_id = :oid
                        WHERE ri.return_id = :rid
                    """), {'oid': order_id, 'rid': return_id}).fetchall()
                    
                    total_expected_refund = 0
                    for item in r_items:
                        if item.order_qty > 0:
                            unit_total = (float(item.taxable_value or 0) + float(item.cgst_amount or 0) + float(item.sgst_amount or 0) + float(item.igst_amount or 0)) / float(item.order_qty)
                            total_expected_refund += unit_total * float(item.return_qty)
                    
                    actual_refund = float(refund_amount)
                    factor = (actual_refund / total_expected_refund) if total_expected_refund > 0 else 0
                    
                    for item in r_items:
                        if item.order_qty > 0:
                            qty_ratio = float(item.return_qty) / float(item.order_qty)
                            final_ratio = qty_ratio * factor
                            
                            ref_taxable = float(item.taxable_value or 0) * final_ratio
                            ref_cgst = float(item.cgst_amount or 0) * final_ratio
                            ref_sgst = float(item.sgst_amount or 0) * final_ratio
                            ref_igst = float(item.igst_amount or 0) * final_ratio
                            
                            ref_total = ref_taxable + ref_cgst + ref_sgst + ref_igst
                            
                            db.session.execute(text("""
                                UPDATE return_items
                                SET refund_taxable_value = :rt, refund_cgst = :rc, refund_sgst = :rs, refund_igst = :ri, refund_total = :rtotal
                                WHERE id = :rid
                            """), {'rt': ref_taxable, 'rc': ref_cgst, 'rs': ref_sgst, 'ri': ref_igst, 'rtotal': ref_total, 'rid': item.ri_id})
                except Exception as ex:
                    logging.info(f"Error calculating refund GST components: {ex}")
            else:
                db.session.execute(text("""
                    UPDATE order_returns
                    SET status = :status, updated_date = NOW()
                    WHERE id = :id
                """), {'status': new_status, 'id': return_id})
            
            db.session.commit()

            try:
                from utils.notifications import trigger_all_order_notifications
                tracking_info: dict = {}
                if new_status == 'rejected':
                    tracking_info['reason'] = rejection_reason
                elif new_status == 'completed':
                    tracking_info['refund_amount'] = refund_amount
                    tracking_info['notes'] = completion_notes
                trigger_all_order_notifications(order_id, f"return_{new_status}", tracking_info)
            except Exception as notif_e:
                logging.info(f"Error triggering return notification: {notif_e}")

            flash(f'Return status updated to {new_status}', 'success')
            return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

        except Exception as e:
            db.session.rollback()
            logging.info(f"Error updating return status: {str(e)}")
            flash('Error updating return status', 'error')
            return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

    except Exception as outer_e:
        logging.error(f"Outer error in update_return_status: {outer_e}")
        flash('Unexpected error updating return status', 'error')
        return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

@admin_bp.route('/orders/<int:order_id>')
@admin_login_required
def admin_order_detail(order_id):
    conn = None
    try:
        logging.info(f"Fetching details for order ID: {order_id}")
        # Validate order_id first
        if not order_id or order_id <= 0:
            flash('Invalid order ID', 'danger')
            return redirect(url_for('admin_bp.admin_orders'))

        try:
            
            # Get order details
            order_res = db.session.execute(text("""
                SELECT o.*, u.username, u.email
                FROM orders o
                JOIN users u ON o.user_id = u.id
                WHERE o.id = :id
            """), {'id': order_id}).fetchone()

            if not order_res:
                flash(f'Order #{order_id} not found', 'danger')
                return redirect(url_for('admin_bp.admin_orders'))

            order = dict(order_res._mapping)

            # Parse JSON addresses
            for key in ['shipping_address', 'billing_address']:
                if order.get(key):
                    try:
                        order[key] = json.loads(order[key])
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Get order items with returned quantity
            items_res = db.session.execute(text("""
                SELECT oi.id, oi.order_id, oi.product_id, oi.quantity, oi.price as unit_price,
                    p.name, p.image, p.sku, p.hsn_code,
                     (SELECT COALESCE(SUM(ri.quantity), 0) 
                      FROM return_items ri 
                      JOIN order_returns orr ON ri.return_id = orr.id 
                      WHERE orr.order_id = oi.order_id AND ri.product_id = oi.product_id) as returned_quantity
                FROM order_items oi
                JOIN products p ON oi.product_id = p.id
                WHERE oi.order_id = :id
            """), {'id': order_id}).fetchall()
            items = [dict(row._mapping) for row in items_res]

            # Get return request if exists
            return_request_res = db.session.execute(text("""
                SELECT * FROM order_returns
                WHERE order_id = :id
                ORDER BY id DESC
                LIMIT 1
            """), {'id': order_id}).fetchone()
            
            return_request = dict(return_request_res._mapping) if return_request_res else None
            
            if return_request and return_request.get('evidence_files'):
                try:
                    return_request['evidence_files'] = json.loads(return_request['evidence_files'])
                except:
                    return_request['evidence_files'] = []

            # Calculate suggested refund amount based on return_items
            suggested_refund = 0
            return_request_items = []
            if return_request:
                # Calculate proportional discount ratio
                discount_ratio = 0
                if order.get('subtotal') and float(order['subtotal']) > 0:
                    discount_ratio = float(order.get('discount_amount', 0)) / float(order['subtotal'])
                
                return_request_items_res = db.session.execute(text("""
                    SELECT ri.quantity, p.name, p.image, p.sku, oi.price
                    FROM return_items ri
                    JOIN products p ON ri.product_id = p.id
                    JOIN order_items oi ON ri.product_id = oi.product_id AND oi.order_id = :oid
                    WHERE ri.return_id = :rid
                """), {'oid': order_id, 'rid': return_request['id']}).fetchall()
                return_request_items = [dict(row._mapping) for row in return_request_items_res]
                
                res = db.session.execute(text("""
                    SELECT SUM(ri.quantity * oi.price) as total
                    FROM return_items ri
                    JOIN order_items oi ON ri.product_id = oi.product_id AND oi.order_id = :oid
                    WHERE ri.return_id = :rid
                """), {'oid': order_id, 'rid': return_request['id']}).fetchone()
                
                if res and res.total:
                    # Refund only the actual amount paid (Price - Proportional Discount)
                    suggested_refund = float(res.total) * (1 - discount_ratio)

            # Fetch return history
            return_history_res = db.session.execute(text("""
                SELECT * FROM order_returns
                WHERE order_id = :id
                ORDER BY id DESC
            """), {'id': order_id}).fetchall()
            return_history = [dict(row._mapping) for row in return_history_res]
            
            for ret in return_history:
                if ret.get('evidence_files'):
                    try:
                        ret['evidence_files'] = json.loads(ret['evidence_files'])
                    except:
                        ret['evidence_files'] = []
                
                # Fetch return items for each history entry
                ret_items_res = db.session.execute(text("""
                    SELECT ri.quantity, p.name, p.image, p.sku, oi.price
                    FROM return_items ri
                    JOIN products p ON ri.product_id = p.id
                    JOIN order_items oi ON ri.product_id = oi.product_id AND oi.order_id = :oid
                    WHERE ri.return_id = :rid
                """), {'oid': order_id, 'rid': ret['id']}).fetchall()
                ret['items'] = [dict(row._mapping) for row in ret_items_res]
            
            # Calculate total returned quantity
            qty_res = db.session.execute(text("""
                SELECT SUM(ri.quantity) as total_qty
                FROM return_items ri
                JOIN order_returns orr ON ri.return_id = orr.id
                WHERE orr.order_id = :id AND orr.status = 'completed'
            """), {'id': order_id}).fetchone()
            total_returned_qty = qty_res.total_qty if qty_res and qty_res.total_qty else 0
            
            # If payment not received, refund should be 0
            if order.get('payment_status') != 'paid':
                suggested_refund = 0
            
            return render_template('admin/order_detail.html',
                                 order=order,
                                 items=items,
                                 return_request=return_request,
                                 return_request_items=return_request_items,
                                 return_history=return_history,
                                 total_returned_qty=total_returned_qty,
                                 suggested_refund=suggested_refund)
        except Exception as e:
            logging.info(f"Error fetching order details: {str(e)}")
            flash(f'Error fetching order details: {str(e)}', 'error')
            return redirect(url_for('admin_bp.admin_orders'))

    except Exception as outer_e:
        logging.error(f"Outer error in admin_order_detail: {outer_e}")
        flash('Unexpected error loading order details', 'error')
        return redirect(url_for('admin_bp.admin_orders'))

@admin_bp.route('/orders/<int:order_id>/cancel', methods=['POST'])
@admin_login_required
def cancel_order(order_id):
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('Cancellation reason is required', 'danger')
        return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

    try:
        
        db.session.execute(text("""
            UPDATE orders
            SET status = 'cancelled',
                cancelled_at = NOW(),
                cancellation_reason = :reason
            WHERE id = :id
        """), {'reason': reason, 'id': order_id})
        
        # Refund Loyalty Points and Gift Cards
        order_info = db.session.execute(text("SELECT user_id, loyalty_points_used, gift_card_id, gift_card_discount FROM orders WHERE id = :id"), {'id': order_id}).fetchone()
        
        if order_info:
            user_id, loyalty_used, gc_id, gc_discount = order_info.user_id, order_info.loyalty_points_used, order_info.gift_card_id, order_info.gift_card_discount
            if loyalty_used and loyalty_used > 0:
                db.session.execute(text("""
                    INSERT INTO loyalty_ledger (user_id, points, transaction_type, order_id)
                    VALUES (:uid, :pts, 'refunded', :oid)
                """), {'uid': user_id, 'pts': loyalty_used, 'oid': order_id})
            if gc_id and gc_discount and gc_discount > 0:
                db.session.execute(text("UPDATE gift_cards SET current_balance = current_balance + :discount WHERE id = :id"), {'discount': gc_discount, 'id': gc_id})
                
        db.session.commit()
        
        try:
            from utils.notifications import trigger_all_order_notifications
            trigger_all_order_notifications(order_id, 'cancelled', {'reason': reason})
        except Exception as notify_e:
            logging.info(f"Failed to trigger cancellation notifications: {notify_e}")
            
        flash('Order cancelled successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error cancelling order: {str(e)}', 'danger')
    return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

@admin_bp.route('/orders/<int:order_id>/process-return', methods=['POST'])
@admin_login_required
def process_return(order_id):
    return_amount = request.form.get('return_amount', type=float)
    reason = request.form.get('reason', '').strip()

    if not reason or return_amount is None:
        flash('Return reason and amount are required', 'danger')
        return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

    try:
        
        db.session.execute(text("""
            UPDATE orders
            SET status = 'refunded',
                returned_at = NOW(),
                return_amount = COALESCE(return_amount, 0) + :amount,
                return_reason = :reason
            WHERE id = :id
        """), {'amount': return_amount, 'reason': reason, 'id': order_id})
        
        # Check if it was a PhonePe payment and initiate refund if needed
        order_info = db.session.execute(text("SELECT payment_method, status FROM orders WHERE id = :id"), {'id': order_id}).fetchone()
        
        if order_info and order_info.payment_method == 'online' and PHONEPE_REFUND_AVAILABLE:
            try:
                phonepe_client = getattr(current_app, 'phonepe_client', None)
                if phonepe_client and RefundRequest is not None:
                    merchant_refund_id = f"REF_{uuid.uuid4().hex[:12].upper()}"
                    amount_paisa = int(return_amount * 100)
                    
                    refund_req = RefundRequest.build_refund_request(
                        merchant_refund_id=merchant_refund_id,
                        original_merchant_order_id=f"OR_{order_id}",
                        amount=amount_paisa
                    )
                    
                    refund_res = phonepe_client.refund(refund_req)
                    
                    # Store refund ID and initial status for reconciliation
                    db.session.execute(text("""
                        UPDATE orders 
                        SET merchant_refund_id = :mid, 
                            refund_status = :state 
                        WHERE id = :id
                    """), {'mid': merchant_refund_id, 'state': refund_res.state, 'id': order_id})
                    
                    flash(f'PhonePe Refund Initiated: {refund_res.state}', 'info')
            except Exception as pe_err:
                logging.info(f"PhonePe Refund Error: {str(pe_err)}")
                flash(f'Refund via PhonePe failed: {str(pe_err)}. Please process manually.', 'warning')

        db.session.commit()
        flash('Return processed successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error processing return: {str(e)}', 'danger')
    return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

@admin_bp.route('/orders/<int:order_id>/cancel-refund', methods=['POST'])
@admin_login_required
def process_cancel_refund(order_id):
    refund_amount = request.form.get('refund_amount', type=float)
    notes = request.form.get('notes', '').strip()
    
    if refund_amount is None:
        flash('Refund amount is required', 'danger')
        return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

    try:
        
        db.session.execute(text("""
            UPDATE orders
            SET return_amount = :amount,
                returned_at = NOW(),
                return_reason = CONCAT('Cancellation Refund: ', :notes)
            WHERE id = :id
        """), {'amount': refund_amount, 'notes': notes, 'id': order_id})

        # Check if it was a PhonePe payment and initiate refund if needed
        order_info = db.session.execute(text("SELECT payment_method FROM orders WHERE id = :id"), {'id': order_id}).fetchone()
        
        if order_info and order_info.payment_method == 'online' and PHONEPE_REFUND_AVAILABLE:
            try:
                phonepe_client = getattr(current_app, 'phonepe_client', None)
                if phonepe_client and RefundRequest is not None:
                    merchant_refund_id = f"REF_CAN_{uuid.uuid4().hex[:12].upper()}"
                    amount_paisa = int(refund_amount * 100)
                    
                    refund_req = RefundRequest.build_refund_request(
                        merchant_refund_id=merchant_refund_id,
                        original_merchant_order_id=f"OR_{order_id}",
                        amount=amount_paisa
                    )
                    
                    refund_res = phonepe_client.refund(refund_req)
                    
                    # Store refund ID and initial status for reconciliation
                    db.session.execute(text("""
                        UPDATE orders 
                        SET merchant_refund_id = :mid, 
                            refund_status = :state 
                        WHERE id = :id
                    """), {'mid': merchant_refund_id, 'state': refund_res.state, 'id': order_id})
                    
                    flash(f'PhonePe Cancellation Refund Initiated: {refund_res.state}', 'info')
            except Exception as pe_err:
                logging.info(f"PhonePe Cancellation Refund Error: {str(pe_err)}")
                flash(f'Refund via PhonePe failed: {str(pe_err)}. Please process manually.', 'warning')

        db.session.commit()
        flash('Cancellation refund processed successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error processing refund: {str(e)}', 'danger')
    return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

@admin_bp.route('/orders/<int:order_id>/update-status', methods=['POST'])
@admin_login_required
def update_order_status(order_id):
    new_status = request.form.get('status')
    if not new_status:
        flash('Status is required', 'danger')
        return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

    # Get additional data for shipping if needed
    courier_name = request.form.get('courier_name', '').strip()
    tracking_id = request.form.get('tracking_id', '').strip()

    try:
        
        # Build the update query based on status
        update_query = "UPDATE orders SET status = :status"
        params = {'status': new_status, 'id': order_id}

        # Set appropriate timestamps based on status
        if new_status == 'processing':
            update_query += ", accepted_at = NOW()"
        elif new_status == 'rtd':
            update_query += ", rtd_at = NOW()"
        elif new_status == 'shipped':
            update_query += ", shipped_at = NOW(), courier_name = :cname, tracking_id = :tid"
            params['cname'] = courier_name
            params['tid'] = tracking_id
        elif new_status == 'delivered':
            update_query += ", delivered_at = NOW()"

        update_query += " WHERE id = :id"

        db.session.execute(text(update_query), params)
        db.session.commit()

        # Handle Invoice Generation if RTD
        if new_status == 'rtd':
            try:
                order_check = db.session.execute(text("SELECT invoice_number FROM orders WHERE id = :id"), {'id': order_id}).fetchone()
                if order_check and not order_check.invoice_number:
                    new_invoice_number = generate_invoice_number_new(db.engine.raw_connection())
                    generate_invoice_pdf(order_id, db.engine.raw_connection(), current_app, new_invoice_number)
            except Exception as inv_e:
                logging.error(f"Error generating invoice on RTD: {inv_e}")
                flash('Order marked as Ready to Dispatch, but invoice generation failed.', 'warning')

        # Award Loyalty Points when order is delivered
        if new_status == 'delivered':
            # Check if points already awarded for this order
            awarded = db.session.execute(text("SELECT id FROM loyalty_ledger WHERE order_id = :id AND transaction_type = 'earned'"), {'id': order_id}).fetchone()
            if not awarded:
                order_info = db.session.execute(text("SELECT user_id, total_amount FROM orders WHERE id = :id"), {'id': order_id}).fetchone()
                if order_info and order_info.total_amount:
                    points_earned = int(float(order_info.total_amount) / 100.0) # 1 point per 100 Rs
                    if points_earned > 0:
                        db.session.execute(text("""
                            INSERT INTO loyalty_ledger (user_id, points, transaction_type, order_id)
                            VALUES (:uid, :pts, 'earned', :oid)
                        """), {'uid': order_info.user_id, 'pts': points_earned, 'oid': order_id})
                        db.session.commit()

        # Trigger Notifications for Supported Events
        supported_events = {
            'processing': 'accepted',
            'rtd': 'rtd',
            'shipped': 'shipped',
            'delivered': 'delivered'
        }
        
        if new_status in supported_events:
            event_name = supported_events[new_status]
            tracking_info = {'courier_name': courier_name, 'tracking_id': tracking_id} if new_status == 'shipped' else None
            
            try:
                # Fire and forget (or could be pushed to a background task queue if one existed)
                trigger_all_order_notifications(order_id, event_name, tracking_info)
            except Exception as notify_e:
                logging.info(f"Failed to trigger notifications for order {order_id}: {notify_e}")

        flash(f'Order status updated to {new_status}', 'success')
    except Exception as err:
        db.session.rollback()
        logging.info(f"Database error: {err}")
        flash('Error updating order status', 'danger')

    return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

@admin_bp.route('/orders/<int:order_id>/update-tracking', methods=['POST'])
@admin_login_required
def update_tracking(order_id):
    courier_name = request.form.get('courier_name', '').strip()
    tracking_id = request.form.get('tracking_id', '').strip()



    try:
        db.session.execute(text("""
            UPDATE orders 
            SET courier_name = :cname, tracking_id = :tid 
            WHERE id = :id
        """), {'cname': courier_name, 'tid': tracking_id, 'id': order_id})
        db.session.commit()
        flash('Tracking information updated successfully', 'success')
    except Exception as err:
        db.session.rollback()
        logging.info(f"Database error updating tracking info: {err}")
        flash('Error updating tracking information', 'danger')

    return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

@admin_bp.route('/orders/<int:order_id>/update-payment', methods=['POST'])
@admin_login_required
def update_payment_status(order_id):
    payment_status = request.form.get('payment_status')
    request.form.get('payment_mode', '')
    request.form.get('transaction_id', '')
    request.form.get('payment_notes', '')

    if not payment_status:
        flash('Payment status is required', 'danger')
        return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

    try:
        db.session.execute(text("UPDATE orders SET payment_status = :status WHERE id = :id"), {'status': payment_status, 'id': order_id})
        db.session.commit()
        flash(f'Payment status updated to {payment_status.title()}', 'success')
    except Exception as err:
        db.session.rollback()
        logging.info(f"Database error updating payment: {err}")
        flash('Error updating payment status', 'danger')

    return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

@admin_bp.route('/orders/sync-external', methods=['POST'])
@admin_login_required
def sync_external_orders():
    try:
        import sys
        import os
        # Add root dir to path if needed so we can import ecommerce_sync
        current_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.abspath(os.path.join(current_dir, '..', '..'))
        if root_dir not in sys.path:
            sys.path.append(root_dir)
            
        from ecommerce_sync import sync_orders_to_database
        sync_orders_to_database()
        
        flash('Successfully synchronized orders from Amazon, Flipkart, and other external channels.', 'success')
    except Exception as e:
        flash(f'Error syncing external orders: {str(e)}', 'danger')
        
    return redirect(url_for('admin_bp.admin_orders'))

import zipfile
import io
from flask import send_file
from invoice_generator import generate_invoice_pdf, generate_bulk_invoices_pdf
from extensions import db

@admin_bp.route('/orders/bulk-update', methods=['POST'])
@admin_login_required
def bulk_update_orders():
    order_ids = request.form.getlist('order_ids')
    action = request.form.get('bulk_action')
    
    if not order_ids or not action:
        flash('No orders or action selected.', 'warning')
        return redirect(request.referrer or url_for('admin_bp.admin_orders'))
        
    try:
        if action in ['processing', 'rtd', 'shipped', 'delivered', 'cancelled']:
            update_query = "UPDATE orders SET status = :status"
            if action == 'processing':
                update_query += ", accepted_at = NOW()"
            elif action == 'rtd':
                update_query += ", rtd_at = NOW()"
            elif action == 'shipped':
                update_query += ", shipped_at = NOW()"
            elif action == 'delivered':
                update_query += ", delivered_at = NOW()"
            elif action == 'cancelled':
                update_query += ", cancelled_at = NOW(), cancellation_reason = 'Bulk Cancelled'"
            update_query += " WHERE id = :id"

            for oid in order_ids:
                db.session.execute(text(update_query), {'status': action, 'id': oid})
            db.session.commit()
            
            from utils.notifications import trigger_all_order_notifications
            for oid in order_ids:
                if action in ['processing', 'rtd', 'shipped', 'delivered']:
                    event_name = 'accepted' if action == 'processing' else action
                    try:
                        trigger_all_order_notifications(oid, event_name, None)
                    except Exception as notif_e:
                        pass
                        
            flash(f'Successfully marked {len(order_ids)} orders as {action.title()}.', 'success')
        elif action == 'paid':
            for oid in order_ids:
                db.session.execute(text("UPDATE orders SET payment_status = 'paid' WHERE id = :id"), {'id': oid})
            db.session.commit()
            flash(f'Successfully marked {len(order_ids)} orders as Paid.', 'success')
        elif action == 'print_invoices':
            buffer, error = generate_bulk_invoices_pdf(order_ids, db.engine.raw_connection(), current_app)
            if buffer:
                return send_file(buffer, mimetype='application/pdf', download_name='bulk_invoices.pdf', as_attachment=False)
            else:
                flash(f'Error generating bulk invoices: {error}', 'danger')
        elif action == 'print_labels':
            from models import Orders, CompanyInfo, OrderItems, Products
            import json
            company = db.session.scalars(db.select(CompanyInfo).limit(1)).first()
            orders_data = []
            for oid in order_ids:
                order = db.session.scalars(db.select(Orders).filter_by(id=oid)).first()
                if order:
                    items_query = db.select(OrderItems, Products.name.label('product_name'), Products.sku).join(Products, OrderItems.product_id == Products.id).filter(OrderItems.order_id == oid)
                    items_raw = db.session.execute(items_query).all()
                    items = []
                    for row in items_raw:
                        items.append({
                            'sku': row.sku,
                            'product_name': row.product_name,
                            'quantity': row[0].quantity
                        })
                    
                    shipping_address = {}
                    if order.shipping_address:
                        try:
                            shipping_address = json.loads(order.shipping_address)
                        except:
                            pass
                            
                    orders_data.append({
                        'order': order,
                        'order_items': items,
                        'shipping_address': shipping_address
                    })
            return render_template('admin/bulk_print_labels.html', orders_data=orders_data, company=company)
        else:
            flash('Invalid bulk action.', 'danger')
            
    except Exception as e:
        db.session.rollback()
        flash(f'Error performing bulk action: {str(e)}', 'danger')
        
    return redirect(request.referrer or url_for('admin_bp.admin_orders'))

import io
import csv
from flask import make_response

@admin_bp.route('/orders/export')
@admin_login_required
def export_orders():
    status_filter = request.args.get('status', 'all')
    search = request.args.get('search', '').strip()
    time_period = request.args.get('time_period', 'all')
    
    query = """
        SELECT o.id, o.order_date, o.total_amount, o.status, o.payment_status,
               u.username as customer, u.email as customer_email, o.tracking_id
        FROM orders o
        JOIN users u ON o.user_id = u.id
        WHERE 1=1
    """
    params: dict = {}

    if status_filter != 'all':
        query += " AND o.status = :status"
        params['status'] = status_filter

    if search:
        query += " AND (o.id LIKE :s1 OR u.username LIKE :s2 OR u.email LIKE :s3 OR o.tracking_id LIKE :s4)"
        params['s1'] = f"%{search}%"
        params['s2'] = f"%{search}%"
        params['s3'] = f"%{search}%"
        params['s4'] = f"%{search}%"

    if time_period == 'today':
        query += " AND DATE(o.order_date) = CURDATE()"
    elif time_period == 'last_2_days':
        query += " AND o.order_date >= DATE_SUB(CURDATE(), INTERVAL 2 DAY)"
    elif time_period == 'last_7_days':
        query += " AND o.order_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)"
    elif time_period == 'this_month':
        query += " AND YEAR(o.order_date) = YEAR(CURDATE()) AND MONTH(o.order_date) = MONTH(CURDATE())"

    query += " ORDER BY o.order_date DESC"
    
    orders_data = db.session.execute(text(query), params).fetchall()

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['Order ID', 'Date', 'Customer', 'Email', 'Amount', 'Status', 'Payment', 'Tracking ID'])
    
    for row in orders_data:
        cw.writerow([row.id, row.order_date, float(row.total_amount or 0), row.customer, row.customer_email, row.status, row.payment_status, row.tracking_id])
        
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=orders_export.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@admin_bp.route('/orders')
@admin_login_required
def admin_orders():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    status_filter = request.args.get('status', 'pending')
    search = request.args.get('search', '').strip()
    time_period = request.args.get('time_period', 'all')

    # Sorting parameters
    sort_column = request.args.get('sort', 'order_date')
    sort_order = request.args.get('order', 'desc')

    # Validate sort column
    valid_sort_columns = ['id', 'order_date', 'customer', 'item_count', 'total_amount', 'status', 'payment_status']
    if sort_column not in valid_sort_columns:
        sort_column = 'order_date'

    # Validate sort order
    if sort_order not in ['asc', 'desc']:
        sort_order = 'desc'

    try:
        
        query = """
            SELECT o.id, o.order_date, o.total_amount, o.status, o.invoice_number, o.payment_status,
                   u.username as customer,
                   COUNT(oi.id) as item_count
            FROM orders o
            JOIN users u ON o.user_id = u.id
            LEFT JOIN order_items oi ON o.id = oi.order_id
            WHERE 1=1
        """
        params: dict = {}

        if status_filter != 'all':
            query += " AND o.status = :status"
            params['status'] = status_filter

        if search:
            query += " AND (o.id LIKE :s1 OR u.username LIKE :s2 OR u.email LIKE :s3 OR o.tracking_id LIKE :s4)"
            params['s1'] = f"%{search}%"
            params['s2'] = f"%{search}%"
            params['s3'] = f"%{search}%"
            params['s4'] = f"%{search}%"

        if time_period == 'today':
            query += " AND DATE(o.order_date) = CURDATE()"
        elif time_period == 'last_2_days':
            query += " AND o.order_date >= DATE_SUB(CURDATE(), INTERVAL 2 DAY)"
        elif time_period == 'last_7_days':
            query += " AND o.order_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)"
        elif time_period == 'this_month':
            query += " AND YEAR(o.order_date) = YEAR(CURDATE()) AND MONTH(o.order_date) = MONTH(CURDATE())"
        elif time_period == 'this_quarter':
            query += " AND YEAR(o.order_date) = YEAR(CURDATE()) AND QUARTER(o.order_date) = QUARTER(CURDATE())"
        elif time_period == 'this_year':
            query += " AND YEAR(o.order_date) = YEAR(CURDATE())"
        elif time_period == 'custom':
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            if start_date:
                query += " AND DATE(o.order_date) >= :start_date"
                params['start_date'] = start_date
            if end_date:
                query += " AND DATE(o.order_date) <= :end_date"
                params['end_date'] = end_date
        elif time_period == 'all':
            pass
        else:
            query += " AND o.order_date >= DATE_SUB(CURDATE(), INTERVAL 2 DAY)"

        query += " GROUP BY o.id"
        
        # Count total
        count_query = f"SELECT COUNT(*) as total FROM ({query}) as subquery"
        total_res = db.session.execute(text(count_query), params).fetchone()
        total = total_res.total if total_res else 0
        
        # Calculate counts for tabs
        status_counts = {'pending': 0, 'processing': 0, 'rtd': 0, 'shipped': 0, 'delivered': 0, 'cancelled': 0}
        
        # If there's a search, filter the counts as well
        if search:
            cnt_query = "SELECT status, COUNT(*) as cnt FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id LIKE :s1 OR u.username LIKE :s2 OR u.email LIKE :s3 OR o.tracking_id LIKE :s4 GROUP BY status"
            counts_res = db.session.execute(text(cnt_query), {'s1': f"%{search}%", 's2': f"%{search}%", 's3': f"%{search}%", 's4': f"%{search}%"}).fetchall()
        else:
            counts_res = db.session.execute(text("SELECT status, COUNT(*) as cnt FROM orders GROUP BY status")).fetchall()
            
        for row in counts_res:
            if row.status in status_counts:
                status_counts[row.status] = row.cnt

        # Add sorting
        order_by_clause = f" ORDER BY {sort_column} {sort_order}"

        # Handle special cases
        if sort_column == 'customer':
            order_by_clause = " ORDER BY u.username " + sort_order
        elif sort_column == 'item_count':
            order_by_clause = " ORDER BY COUNT(oi.id) " + sort_order
        
        query += order_by_clause
        
        # Pagination
        offset = (page - 1) * per_page
        query += f" LIMIT {per_page} OFFSET {offset}"

        orders_data = db.session.execute(text(query), params).fetchall()

        # Format orders
        formatted_orders = []
        for o in orders_data:
            formatted_orders.append({
                'id': o.id,
                'order_date': o.order_date,
                'total_amount': float(o.total_amount) if o.total_amount else 0.0,
                'status': o.status,
                'invoice_number': o.invoice_number,
                'payment_status': o.payment_status,
                'customer': o.customer,
                'item_count': o.item_count
            })

        class Pagination:
            def __init__(self, page, per_page, total):
                self.page = page
                self.per_page = per_page
                self.total = total
                self.pages = (total + per_page - 1) // per_page

        pagination = Pagination(page, per_page, total)

        return render_template('admin/orders.html',
                               orders=formatted_orders,
                               pagination=pagination,
                               status_filter=status_filter,
                               search=search,
                               time_period=time_period,
                               sort_column=sort_column,
                               current_order=sort_order,
                               status_counts=status_counts)

    except Exception as err:
        logging.info(f"Database error: {err}")
        flash('Error retrieving orders', 'danger')
        return render_template('admin/orders.html', orders=[], pagination={})

@admin_bp.route('/orders/<int:order_id>/generate-invoice', methods=['POST'])
@admin_login_required
def generate_invoice(order_id):
    """Generate invoice for an order"""
    try:
        
        # Check if order exists and is in a valid state for invoicing
        order_res = db.session.execute(text("""
            SELECT status, invoice_number
            FROM orders
            WHERE id = :id
        """), {'id': order_id}).fetchone()

        if not order_res:
            flash('Order not found', 'danger')
            return redirect(url_for('admin_bp.admin_orders'))

        order = dict(order_res._mapping)

        if order['invoice_number']:
            flash('Invoice already generated for this order', 'info')
            return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

        if order['status'] not in ['processing', 'shipped', 'delivered']:
            flash('Invoice can only be generated for processing, shipped or delivered orders', 'warning')
            return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

        # We will need the raw connection for generate_invoice_number_new and generate_invoice_pdf
        # since they still expect a raw connection.
        conn = db.engine.raw_connection()
        try:
            # Generate invoice number FIRST
            invoice_number = generate_invoice_number_new(conn)

            # Generate invoice with the pre-generated invoice number
            pdf_buffer, error = generate_invoice_pdf(order_id, conn, current_app, invoice_number)
            if error:
                flash(f'Error generating invoice: {error}', 'danger')
                return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))
        finally:
            conn.close()

        db.session.execute(text("""
            UPDATE orders
            SET invoice_number = :inv, invoice_date = NOW()
            WHERE id = :id
        """), {'inv': invoice_number, 'id': order_id})

        db.session.commit()
        flash('Invoice generated successfully', 'success')

    except Exception as e:
        db.session.rollback()
        logging.info(f"Error generating invoice: {e}")
        flash('Error generating invoice', 'danger')

    return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

@admin_bp.route('/orders/<int:order_id>/download-invoice')
@admin_login_required
def download_invoice(order_id):
    """Download generated invoice"""
    try:
        
        order_res = db.session.execute(text("""
            SELECT invoice_number
            FROM orders
            WHERE id = :id
        """), {'id': order_id}).fetchone()

        if not order_res or not order_res.invoice_number:
            flash('Invoice not found', 'danger')
            return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))

        # We will need the raw connection for generate_invoice_pdf
        conn = db.engine.raw_connection()
        try:
            pdf_buffer, error = generate_invoice_pdf(order_id, conn, current_app, order_res.invoice_number)
            if error:
                flash(f'Error generating invoice: {error}', 'danger')
                return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))
        finally:
            conn.close()

        return send_file(
            pdf_buffer,
            as_attachment=False,
            download_name=f'invoice_{order_res.invoice_number}.pdf',
            mimetype='application/pdf'
        )

    except Exception as e:
        logging.info(f"Error downloading invoice: {e}")
        flash('Error downloading invoice', 'danger')
        return redirect(url_for('admin_bp.admin_order_detail', order_id=order_id))


@admin_bp.route('/orders/<int:order_id>/print-label', methods=['GET'])
@admin_login_required
def print_shipping_label(order_id):
    from models import Orders, CompanyInfo, OrderItems, Products
    import json
    
    order = db.session.scalars(db.select(Orders).filter_by(id=order_id)).first()
    
    items_query = db.select(OrderItems, Products.name.label('product_name'), Products.sku).join(Products, OrderItems.product_id == Products.id).filter(OrderItems.order_id == order_id)
    items_raw = db.session.execute(items_query).all()
    items = []
    for row in items_raw:
        order_item = row[0]
        items.append({
            'sku': row.sku,
            'product_name': row.product_name,
            'quantity': order_item.quantity
        })
    if not order:
        flash('Order not found', 'danger')
        return redirect(url_for('admin_bp.admin_orders'))
        
    shipping_address = {}
    if order.shipping_address:
        try:
            shipping_address = json.loads(order.shipping_address)
        except:
            pass
            
    company = db.session.scalars(db.select(CompanyInfo).limit(1)).first()
    
    return render_template('admin/print_label.html', 
                          order=order, 
                          shipping_address=shipping_address, 
                          company=company,
                          items=items)
