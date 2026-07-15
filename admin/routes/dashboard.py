from datetime import datetime, timedelta
from flask import render_template, request, session, redirect, url_for
from sqlalchemy import text # type: ignore
from extensions import db
from admin.admin_app import admin_bp, admin_login_required

@admin_bp.route('/dashboard')
@admin_login_required
def admin_dashboard():
    # URL Parameters
    admin_role = session.get('admin_role', 'admin')
    if hasattr(admin_role, 'value'):
        admin_role = admin_role.value
    admin_role = str(admin_role).lower()
    
    # Determine allowed tabs based on role
    role_tabs = {
        'admin': ['business', 'products', 'orders', 'analytics'],
        'manager': ['business', 'products', 'orders', 'analytics'],
        'sales': ['business', 'products'],
        'operations': ['orders'],
        'mis': ['business', 'analytics'],
        'analytics': ['analytics', 'business'],
        'inventory': ['products', 'orders'],
        'payment': ['business'],
        'storefront': ['products', 'analytics'],
        'editor': ['products']
    }
    allowed_tabs = role_tabs.get(admin_role, ['business'])
    
    tab = request.args.get('tab')
    if not tab or tab not in allowed_tabs:
        tab = allowed_tabs[0] if allowed_tabs else 'business'

    days_range = int(request.args.get('range', 30))
    
    # Dates for querying
    now = datetime.now()
    start_date = now - timedelta(days=days_range)
    prev_start_date = now - timedelta(days=days_range * 2)
    
    updated_at = now.strftime('%Y-%m-%d %H:%M')

    # Default Data Structures
    from typing import Any
    data: dict[str, Any] = {
        'admin_role': admin_role,
        'allowed_tabs': allowed_tabs,
        'active_tab': tab,
        'updated_at': updated_at,
        'total_revenue': 0,
        'revenue_change': 0,
        'total_orders': 0,
        'order_change': 0,
        'new_customers': [],
        'customer_change': 0,
        'conversion_rate': 0.0,
        'conversion_change': 0,
        'product_counts': {'active': 0, 'inactive': 0},
        'open_orders_count': 0,
        'order_statuses': [],
        'return_stats': {'total_returns': 0, 'pending_returns': 0, 'completed_returns': 0},
        'revenue_labels': [],
        'revenue_data': [],
        'order_data': [],
        'recent_activities': [],
        'feedback_rating': {'avg_rating': 0.0, 'positive': 0, 'negative': 0},
        'listing_status': {'active': 0, 'inactive': 0, 'out_of_stock': 0},
        'total_sessions': 0,
        'product_performance': [],
        'aged_inventory': {'age_0_90': 0, 'age_91_180': 0, 'age_181_270': 0, 'age_271_365': 0, 'age_365_plus': 0},
        'category_performance': [],
        'customer_acquisition_labels': [],
        'customer_acquisition_data': [],
        'sales_by_region_labels': [],
        'sales_by_region_data': [],
        'top_customers': [],
        'low_stock_alerts': []
    }

    try:
        # --- 1. Business Metrics ---
        if True:  # Changed to always compute so top tabs are populated
            # Current Period Revenue & Orders
            current_stats = db.session.execute(text("""
                SELECT COALESCE(SUM(total_amount), 0) as revenue, COUNT(id) as orders
                FROM orders
                WHERE order_date >= :start_date AND status IN ('Delivered', 'Shipped', 'Processing')
            """), {'start_date': start_date}).fetchone()
            
            # Previous Period Revenue & Orders
            prev_stats = db.session.execute(text("""
                SELECT COALESCE(SUM(total_amount), 0) as revenue, COUNT(id) as orders
                FROM orders
                WHERE order_date >= :prev_start AND order_date < :start_date AND status IN ('Delivered', 'Shipped', 'Processing')
            """), {'start_date': start_date, 'prev_start': prev_start_date}).fetchone()

            data['total_revenue'] = float(current_stats.revenue) if current_stats else 0.0
            data['total_orders'] = current_stats.orders if current_stats else 0
            
            prev_rev = float(prev_stats.revenue) if prev_stats and prev_stats.revenue else 0.0
            prev_ord = prev_stats.orders if prev_stats else 0
            
            if prev_rev > 0:
                data['revenue_change'] = round(((data['total_revenue'] - prev_rev) / prev_rev) * 100, 1)
            else:
                data['revenue_change'] = 100.0 if data['total_revenue'] > 0 else 0.0
                
            if prev_ord > 0:
                data['order_change'] = round(((data['total_orders'] - prev_ord) / prev_ord) * 100, 1)
            else:
                data['order_change'] = 100.0 if data['total_orders'] > 0 else 0.0

            # Customers
            new_cust_query = db.session.execute(text("""
                SELECT id FROM users WHERE created_at >= :start_date
            """), {'start_date': start_date}).fetchall()
            data['new_customers'] = [row.id for row in new_cust_query]
            
            prev_cust_query = db.session.execute(text("""
                SELECT COUNT(id) as user_count FROM users WHERE created_at >= :prev_start AND created_at < :start_date
            """), {'start_date': start_date, 'prev_start': prev_start_date}).fetchone()
            prev_cust = prev_cust_query.user_count if prev_cust_query else 0
            current_cust = len(data['new_customers'])
            
            if prev_cust > 0:
                data['customer_change'] = round(((current_cust - prev_cust) / prev_cust) * 100, 1)
            else:
                data['customer_change'] = 100.0 if current_cust > 0 else 0.0

            # Sessions for Conversion Rate
            sessions_query = db.session.execute(text("""
                SELECT COUNT(session_id) as session_count FROM user_sessions WHERE created_at >= :start_date
            """), {'start_date': start_date}).fetchone()
            data['total_sessions'] = sessions_query.session_count if sessions_query else 1
            
            if data['total_sessions'] > 0:
                data['conversion_rate'] = (data['total_orders'] / data['total_sessions']) * 100

        # --- 2. Chart Data ---
        if True:  # Changed to always compute for main dashboard
            chart_query = db.session.execute(text("""
                SELECT DATE(order_date) as dt, COALESCE(SUM(total_amount), 0) as daily_revenue, COUNT(id) as daily_orders
                FROM orders
                WHERE order_date >= :start_date AND status IN ('Delivered', 'Shipped', 'Processing')
                GROUP BY DATE(order_date)
                ORDER BY dt ASC
            """), {'start_date': start_date}).fetchall()
            
            # Fill in missing dates to make the chart smooth
            dates_dict = {}
            curr = start_date
            while curr <= now:
                dates_dict[curr.strftime('%Y-%m-%d')] = {'revenue': 0.0, 'orders': 0}
                curr += timedelta(days=1)
                
            for row in chart_query:
                dt_str = row.dt.strftime('%Y-%m-%d')
                if dt_str in dates_dict:
                    dates_dict[dt_str]['revenue'] = float(row.daily_revenue)
                    dates_dict[dt_str]['orders'] = row.daily_orders
                    
            for dt_str, vals in dates_dict.items():
                data['revenue_labels'].append(dt_str)
                data['revenue_data'].append(vals['revenue'])
                data['order_data'].append(vals['orders'])

        # --- 3. Order Statuses ---
        statuses_query = db.session.execute(text("""
            SELECT status, COUNT(id) as status_count FROM orders WHERE order_date >= :start_date GROUP BY status
        """), {'start_date': start_date}).fetchall()
        
        status_map: dict[str, int] = {'Pending': 0, 'Processing': 0, 'Shipped': 0, 'Delivered': 0, 'Cancelled': 0}
        for row in statuses_query:
            status_title = str(row.status).title()
            if status_title == 'Completed':
                status_title = 'Delivered'
            
            if status_title in status_map:
                status_map[status_title] += row.status_count
            else:
                status_map[status_title] = row.status_count
                
        for name, count in status_map.items():
            data['order_statuses'].append({'name': name, 'count': count})
            if name in ('Pending', 'Processing'):
                data['open_orders_count'] += count

        # --- 4. Recent Activities ---
        recent_orders = db.session.execute(text("""
            SELECT o.id, u.username as customer_name, o.total_amount, o.status
            FROM orders o
            LEFT JOIN users u ON o.user_id = u.id
            ORDER BY o.order_date DESC LIMIT 5
        """)).fetchall()
        
        for ro in recent_orders:
            color = 'primary'
            icon = 'fa-shopping-cart'
            if ro.status == 'Delivered':
                color = 'success'
                icon = 'fa-check'
            elif ro.status == 'Cancelled':
                color = 'danger'
                icon = 'fa-times'
            elif ro.status == 'Shipped':
                color = 'info'
                icon = 'fa-truck'
                
            data['recent_activities'].append({
                'type': 'order',
                'title': f'Order #{ro.id}',
                'customer_name': ro.customer_name or 'Guest',
                'amount': f'₹{ro.total_amount}',
                'color': color,
                'icon': icon,
                'status': ro.status,
                'link': url_for('admin_bp.admin_order_detail', order_id=ro.id)
            })

        # --- 5. Return Stats ---
        returns_query = db.session.execute(text("""
            SELECT status, COUNT(id) as count FROM order_returns GROUP BY status
        """)).fetchall()
        
        for row in returns_query:
            data['return_stats']['total_returns'] += row.count
            if row.status.lower() in ('pending', 'initiated'):
                data['return_stats']['pending_returns'] += row.count
            elif row.status.lower() in ('completed', 'refunded'):
                data['return_stats']['completed_returns'] += row.count

        # --- 6. Products & Inventory ---
        if tab in ['products', 'business', 'analytics', 'orders']:
            product_stats = db.session.execute(text("""
                SELECT 
                    SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active_count,
                    SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END) as inactive_count,
                    SUM(CASE WHEN stock_quantity <= 0 THEN 1 ELSE 0 END) as oos_count
                FROM products
            """)).fetchone()
            
            if product_stats:
                data['product_counts']['active'] = product_stats.active_count or 0
                data['product_counts']['inactive'] = product_stats.inactive_count or 0
                data['listing_status']['active'] = product_stats.active_count or 0
                data['listing_status']['inactive'] = product_stats.inactive_count or 0
                data['listing_status']['out_of_stock'] = product_stats.oos_count or 0

        # Top Performing Products
        if tab in ['products', 'business', 'analytics']:
            top_products = db.session.execute(text("""
                SELECT p.id, p.name, p.sku, p.image, p.is_active, p.stock_quantity, p.price,
                       SUM(oi.quantity) as total_sold, SUM(oi.price * oi.quantity) as revenue
                FROM order_items oi
                JOIN products p ON oi.product_id = p.id
                JOIN orders o ON oi.order_id = o.id
                WHERE o.order_date >= :start_date AND o.status NOT IN ('Cancelled')
                GROUP BY p.id
                ORDER BY total_sold DESC
                LIMIT 5
            """), {'start_date': start_date}).fetchall()
            
            for p in top_products:
                data['product_performance'].append({
                    'id': p.id, 'name': p.name, 'sku': p.sku, 'image': p.image,
                    'is_active': bool(p.is_active), 'stock_quantity': p.stock_quantity,
                    'price': float(p.price), 'total_sold': p.total_sold, 'revenue': float(p.revenue)
                })

        # Aged Inventory
        if tab in ['products']:
            aged_query = db.session.execute(text("""
                SELECT 
                    SUM(CASE WHEN DATEDIFF(NOW(), created_at) <= 90 THEN 1 ELSE 0 END) as age_0_90,
                    SUM(CASE WHEN DATEDIFF(NOW(), created_at) BETWEEN 91 AND 180 THEN 1 ELSE 0 END) as age_91_180,
                    SUM(CASE WHEN DATEDIFF(NOW(), created_at) BETWEEN 181 AND 270 THEN 1 ELSE 0 END) as age_181_270,
                    SUM(CASE WHEN DATEDIFF(NOW(), created_at) BETWEEN 271 AND 365 THEN 1 ELSE 0 END) as age_271_365,
                    SUM(CASE WHEN DATEDIFF(NOW(), created_at) > 365 THEN 1 ELSE 0 END) as age_365_plus
                FROM products
                WHERE stock_quantity > 0
            """)).fetchone()
            
            if aged_query:
                data['aged_inventory'] = {
                    'age_0_90': aged_query.age_0_90 or 0,
                    'age_91_180': aged_query.age_91_180 or 0,
                    'age_181_270': aged_query.age_181_270 or 0,
                    'age_271_365': aged_query.age_271_365 or 0,
                    'age_365_plus': aged_query.age_365_plus or 0
                }

        # --- 7. Testimonials Feedback ---
        feedback_query = db.session.execute(text("""
            SELECT 
                AVG(rating) as avg_rating,
                SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) as positive,
                SUM(CASE WHEN rating <= 3 THEN 1 ELSE 0 END) as negative
            FROM customer_testimonials
        """)).fetchone()
        
        if feedback_query and feedback_query.avg_rating:
            data['feedback_rating'] = {
                'avg_rating': float(feedback_query.avg_rating),
                'positive': feedback_query.positive or 0,
                'negative': feedback_query.negative or 0
            }

        # --- 8. Category Performance ---
        if tab in ['business', 'analytics', 'products']:
            cat_query = db.session.execute(text("""
                SELECT p.category, COALESCE(SUM(oi.price * oi.quantity), 0) as revenue
                FROM order_items oi
                JOIN products p ON oi.product_id = p.id
                JOIN orders o ON oi.order_id = o.id
                WHERE o.order_date >= :start_date AND o.status NOT IN ('Cancelled') AND p.category IS NOT NULL
                GROUP BY p.category
                ORDER BY revenue DESC
                LIMIT 5
            """), {'start_date': start_date}).fetchall()
            for row in cat_query:
                data['category_performance'].append({'category': row.category, 'revenue': float(row.revenue)})

        # --- 9. Customer Acquisition ---
        if tab in ['business', 'analytics']:
            acq_query = db.session.execute(text("""
                SELECT DATE(created_at) as dt, COUNT(id) as daily_customers
                FROM users
                WHERE created_at >= :start_date
                GROUP BY DATE(created_at)
                ORDER BY dt ASC
            """), {'start_date': start_date}).fetchall()
            
            dates_dict_acq = {}
            curr = start_date
            while curr <= now:
                dates_dict_acq[curr.strftime('%Y-%m-%d')] = 0
                curr += timedelta(days=1)
                
            for row in acq_query:
                dt_str = row.dt.strftime('%Y-%m-%d')
                if dt_str in dates_dict_acq:
                    dates_dict_acq[dt_str] = row.daily_customers
                    
            for dt_str, count in dates_dict_acq.items():
                data['customer_acquisition_labels'].append(dt_str)
                data['customer_acquisition_data'].append(count)

        # --- 10. Sales by Region ---
        if tab in ['business', 'analytics']:
            orders_for_region = db.session.execute(text("""
                SELECT shipping_address
                FROM orders
                WHERE order_date >= :start_date AND status NOT IN ('Cancelled')
            """), {'start_date': start_date}).fetchall()
            
            import json
            region_counts = {}
            for row in orders_for_region:
                if not row.shipping_address: continue
                try:
                    addr = json.loads(row.shipping_address)
                    state = addr.get('state', 'Unknown').title()
                    if state and state != 'Unknown':
                        region_counts[state] = region_counts.get(state, 0) + 1
                except:
                    pass

            top_regions = sorted(region_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            for region, count in top_regions:
                data['sales_by_region_labels'].append(region)
                data['sales_by_region_data'].append(count)

        # --- 11. Top Customers ---
        if tab in ['business', 'analytics', 'products']:
            top_cust_query = db.session.execute(text("""
                SELECT u.username as name, u.email, COUNT(o.id) as total_orders, COALESCE(SUM(o.total_amount), 0) as revenue
                FROM orders o
                JOIN users u ON o.user_id = u.id
                WHERE o.order_date >= :start_date AND o.status NOT IN ('Cancelled')
                GROUP BY u.id
                ORDER BY revenue DESC
                LIMIT 5
            """), {'start_date': start_date}).fetchall()
            for row in top_cust_query:
                data['top_customers'].append({'name': row.name, 'email': row.email, 'total_orders': row.total_orders, 'revenue': float(row.revenue)})

        # --- 12. Low Stock Alerts ---
        if tab in ['products', 'business', 'orders', 'analytics']:
            low_stock_query = db.session.execute(text("""
                SELECT id, name, sku, stock_quantity, reorder_level
                FROM products
                WHERE stock_quantity <= reorder_level AND is_active = 1
                ORDER BY stock_quantity ASC
                LIMIT 5
            """)).fetchall()
            for row in low_stock_query:
                data['low_stock_alerts'].append({
                    'id': row.id, 'name': row.name, 'sku': row.sku,
                    'stock_quantity': row.stock_quantity, 'reorder_level': row.reorder_level
                })

    except Exception as e:
        print(f"Error generating dashboard data: {e}")

    return render_template('admin/dashboard.html', **data)
