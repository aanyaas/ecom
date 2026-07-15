import bcrypt
import mysql.connector
from flask import render_template, request, redirect, url_for, flash, session, jsonify, render_template_string
from admin.admin_app import (
    admin_bp, admin_login_required, AdminUserForm
)

import requests as _requests
from extensions import db
from models import Users, AdminUsers
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helper functions for session management
# ---------------------------------------------------------------------------

def get_geolocation(ip_address):
    """Fetch country/city/region for an IP via ip-api.com (free, no key needed)."""
    try:
        resp = _requests.get(
            f'http://ip-api.com/json/{ip_address}',
            timeout=3,
            params={'fields': 'status,country,regionName,city'}
        )
        data = resp.json()
        if data.get('status') == 'success':
            return {
                'country': data.get('country', 'Unknown'),
                'region':  data.get('regionName', 'Unknown'),
                'city':    data.get('city', 'Unknown'),
            }
    except Exception:
        pass
    return {'country': 'Unknown', 'region': 'Unknown', 'city': 'Unknown'}


def get_session_stats():
    """Return aggregate statistics from the user_sessions table."""
    try:
        from sqlalchemy import text
        query = text("""
            SELECT
                COUNT(*) AS total_sessions,
                SUM(CASE WHEN last_activity > DATE_SUB(NOW(), INTERVAL 1 HOUR) AND logout_time IS NULL THEN 1 ELSE 0 END) AS active_sessions,
                SUM(CASE WHEN user_id IS NULL AND logout_time IS NULL THEN 1 ELSE 0 END) AS guest_sessions,
                SUM(CASE WHEN logout_time IS NOT NULL THEN 1 ELSE 0 END) AS logged_out_sessions
            FROM user_sessions
        """)
        result = db.session.execute(query).fetchone()
        if result:
            return {
                'total_sessions': result.total_sessions,
                'active_sessions': result.active_sessions,
                'guest_sessions': result.guest_sessions,
                'logged_out_sessions': result.logged_out_sessions
            }
        return {}
    except Exception as e:
        print(f"Error fetching session stats: {e}")
        return {}


def cleanup_old_sessions(days=30):
    """Delete sessions older than *days* days and return the number of rows removed."""
    try:
        from sqlalchemy import text
        query = text("DELETE FROM user_sessions WHERE last_activity < DATE_SUB(NOW(), INTERVAL :days DAY)")
        result = db.session.execute(query, {'days': days})
        deleted = result.rowcount  # type: ignore
        db.session.commit()
        return deleted
    except Exception as e:
        print(f"Error cleaning up sessions: {e}")
        db.session.rollback()
        return 0


# ---------------------------------------------------------------------------

@admin_bp.route('/sessions')
@admin_login_required
def admin_sessions():
    """Enhanced session management view"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    filter_type = request.args.get('filter', 'all')
    search = request.args.get('search', '').strip()  # type: ignore
    try:
        from sqlalchemy import text
        # Build query based on filter and search
        query = """
            SELECT s.*,
                   COALESCE(u.username, 'Guest') as username,
                   TIMESTAMPDIFF(MINUTE, s.created_at, COALESCE(s.logout_time, s.last_activity)) as duration_minutes,
                   CASE
                       WHEN s.last_activity > DATE_SUB(NOW(), INTERVAL 15 MINUTE) THEN 'Online'
                       WHEN s.last_activity > DATE_SUB(NOW(), INTERVAL 1 HOUR) THEN 'Recently Active'
                       ELSE 'Inactive'
                   END as status
            FROM user_sessions s
            LEFT JOIN admin_users u ON s.user_id = u.id
            WHERE 1=1
        """
        params = {}

        # Apply filters
        if filter_type == 'active':
            query += " AND s.last_activity > DATE_SUB(NOW(), INTERVAL 1 HOUR) AND s.logout_time IS NULL"
        elif filter_type == 'logged_in':
            query += " AND s.login_successful = 1 AND s.logout_time IS NULL"
        elif filter_type == 'logged_out':
            query += " AND s.logout_time IS NOT NULL"
        elif filter_type == 'suspicious':
            query += " AND (s.device_type = 'Bot' OR s.user_agent LIKE '%bot%' OR s.ip_address IS NULL)"

        # Apply search
        if search:
            query += " AND (s.ip_address LIKE :s1 OR s.country LIKE :s2 OR s.city LIKE :s3 OR u.username LIKE :s4)"
            search_param = f"%{search}%"
            params.update({'s1': search_param, 's2': search_param, 's3': search_param, 's4': search_param})  # type: ignore

        # Count total
        count_query = f"SELECT COUNT(*) as total FROM ({query}) as subquery"
        total_res = db.session.execute(text(count_query), params).fetchone()
        total = total_res.total if total_res else 0

        # Get sessions with pagination
        query += " ORDER BY s.last_activity DESC LIMIT :limit OFFSET :offset"
        params.update({'limit': per_page, 'offset': (page - 1) * per_page})  # type: ignore

        sessions_res = db.session.execute(text(query), params).fetchall()
        sessions = [dict(row._mapping) for row in sessions_res]

        # Get session statistics
        stats = get_session_stats()

        # Create a simple pagination object with iter_pages method
        class SimplePagination:
            def __init__(self, page, per_page, total):
                self.page = page
                self.per_page = per_page
                self.total = total
                self.pages = (total + per_page - 1) // per_page

            def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
                last = 0
                for num in range(1, self.pages + 1):
                    if (num <= left_edge or
                        (num > self.page - left_current - 1 and num < self.page + right_current) or
                        num > self.pages - right_edge):
                        if last + 1 != num:
                            yield None
                        yield num
                        last = num

        pagination = SimplePagination(page, per_page, total)

        # Get suspicious sessions for the alert banner
        suspicious_res = db.session.execute(text("SELECT session_id FROM user_sessions WHERE device_type = 'Bot' OR user_agent LIKE '%bot%' OR ip_address IS NULL LIMIT 5")).fetchall()
        suspicious_sessions = [{'session_id': row.session_id} for row in suspicious_res]

        return render_template('admin/sessions.html',
                             sessions=sessions,
                             pagination=pagination,
                             filter_type=filter_type,
                             search=search,
                             total_sessions=stats.get('total_sessions', 0),
                             active_sessions=stats.get('active_sessions', 0),
                             guest_sessions=stats.get('guest_sessions', 0),
                             logged_out_sessions=stats.get('logged_out_sessions', 0),
                             suspicious_sessions=suspicious_sessions)

    except Exception as err:
        print(f"Database error: {err}")
        flash('Error retrieving sessions', 'danger')
        return render_template('admin/sessions.html', sessions=[], pagination=None, stats={})

@admin_bp.route('/sessions/cleanup', methods=['POST'])
@admin_login_required
def cleanup_sessions():
    """Enhanced session cleanup with better feedback"""
    if request.is_json:
        data = request.get_json()
        logged_out = data.get('logged_out', False)
        inactive = data.get('inactive', False)
        guest = data.get('guest', False)
        
        try:
            from sqlalchemy import text
            deleted_count = 0
            
            if logged_out:
                result = db.session.execute(text("DELETE FROM user_sessions WHERE logout_time IS NOT NULL AND last_activity < DATE_SUB(NOW(), INTERVAL 7 DAY)"))
                deleted_count += result.rowcount  # type: ignore
            if inactive:
                result = db.session.execute(text("DELETE FROM user_sessions WHERE last_activity < DATE_SUB(NOW(), INTERVAL 30 DAY)"))
                deleted_count += result.rowcount  # type: ignore
            if guest:
                result = db.session.execute(text("DELETE FROM user_sessions WHERE user_id IS NULL AND last_activity < DATE_SUB(NOW(), INTERVAL 1 DAY)"))
                deleted_count += result.rowcount  # type: ignore
            db.session.commit()
            if deleted_count > 0:
                flash(f'Successfully cleaned up {deleted_count} sessions.', 'success')
            else:
                flash('No sessions met the cleanup criteria.', 'info')
            return jsonify({'success': True, 'deleted': deleted_count})
        except Exception as e:
            print(f"Error cleaning up sessions: {e}")
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    # Fallback for old form submission
    days = request.form.get('days', 30, type=int)
    if days < 1:
        flash('Days must be at least 1', 'danger')
        return redirect(url_for('admin_bp.admin_sessions'))

    deleted_count = cleanup_old_sessions(days)
    if deleted_count > 0:
        flash(f'Successfully cleaned up {deleted_count} sessions older than {days} days', 'success')
    else:
        flash(f'No sessions found older than {days} days', 'info')
    return redirect(url_for('admin_bp.admin_sessions'))

@admin_bp.route('/sessions/refresh-geolocation', methods=['POST'])
@admin_login_required
def refresh_geolocation():
    """Refresh geolocation data for sessions with unknown location"""
    try:
        from sqlalchemy import text
        # Get sessions with unknown or missing geolocation
        query = text("""
            SELECT session_id, ip_address
            FROM user_sessions
            WHERE (country IS NULL OR country = 'Unknown' OR country = '')
            AND ip_address IS NOT NULL
            AND ip_address != '127.0.0.1'
            AND last_activity > DATE_SUB(NOW(), INTERVAL 7 DAY)
            LIMIT 50
        """)
        sessions = db.session.execute(query).fetchall()

        updated_count = 0
        for session_data in sessions:
            geo_data = get_geolocation(session_data.ip_address)
            if geo_data.get('country') and geo_data.get('country') != 'Unknown':
                update_query = text("""
                    UPDATE user_sessions
                    SET country = :country, city = :city, region = :region
                    WHERE session_id = :session_id
                """)
                db.session.execute(update_query, {
                    'country': geo_data.get('country'),
                    'city': geo_data.get('city', 'Unknown'),
                    'region': geo_data.get('region', 'Unknown'),
                    'session_id': session_data.session_id
                })
                updated_count += 1

        db.session.commit()
        return jsonify({
            'success': True,
            'message': f'Updated geolocation for {updated_count} sessions',
            'updated': updated_count
        })
    except Exception as e:
        print(f"Error refreshing geolocation: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Error updating geolocation data'}), 500

@admin_bp.route('/sessions/<session_id>/terminate', methods=['POST'])
@admin_login_required
def terminate_session(session_id):
    try:
        from sqlalchemy import text
        result = db.session.execute(text("UPDATE user_sessions SET logout_time = NOW() WHERE session_id = :session_id"), {'session_id': session_id})
        if result.rowcount > 0:  # type: ignore
            db.session.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Session not found'}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/sessions/<session_id>/details', methods=['GET'])
@admin_login_required
def session_details(session_id):
    try:
        from sqlalchemy import text
        query = text("""
            SELECT s.*, u.username, u.email
            FROM user_sessions s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE s.session_id = :session_id
        """)
        session_data = db.session.execute(query, {'session_id': session_id}).fetchone()
        if not session_data:
            return jsonify({'success': False, 'message': 'Session not found'}), 404
            
        html = render_template_string('''
            <table class="table table-bordered">
                <tbody>
                    <tr><th class="bg-light" style="width: 30%">Session ID</th><td><code>{{ s.session_id }}</code></td></tr>
                    <tr><th class="bg-light">User</th><td>{{ s.username or 'Guest' }} {% if s.email %}<span class="text-muted">({{ s.email }})</span>{% endif %}</td></tr>
                    <tr><th class="bg-light">IP Address</th><td><code>{{ s.ip_address }}</code></td></tr>
                    <tr><th class="bg-light">Location</th><td>{{ s.city }}, {{ s.region }}, {{ s.country }}</td></tr>
                    <tr><th class="bg-light">Device / Platform</th><td>{{ s.device_type }} / {{ s.platform }}</td></tr>
                    <tr><th class="bg-light">Browser</th><td>{{ s.browser }} {{ s.browser_version }}</td></tr>
                    <tr><th class="bg-light">User Agent</th><td><small class="text-muted">{{ s.user_agent }}</small></td></tr>
                    <tr><th class="bg-light">Started At</th><td>{{ s.created_at.strftime('%Y-%m-%d %H:%M:%S') if s.created_at else 'N/A' }}</td></tr>
                    <tr><th class="bg-light">Last Activity</th><td>{{ s.last_activity.strftime('%Y-%m-%d %H:%M:%S') if s.last_activity else 'N/A' }}</td></tr>
                    <tr><th class="bg-light">Status</th><td>
                        {% if s.logout_time %}
                            <span class="badge badge-danger">Terminated at {{ s.logout_time.strftime('%Y-%m-%d %H:%M:%S') }}</span>
                        {% elif s.login_successful %}
                            <span class="badge badge-success">Active Logged In</span>
                        {% else %}
                            <span class="badge badge-warning">Active Guest</span>
                        {% endif %}
                    </td></tr>
                </tbody>
            </table>
        ''', s=dict(session_data._mapping))
        
        return jsonify({'success': True, 'html': html})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/sessions/block-ip', methods=['POST'])
@admin_login_required
def block_ip():
    if request.is_json:
        data = request.get_json()
        ip_address = data.get('ip_address')
        if not ip_address:
            return jsonify({'success': False, 'message': 'No IP address provided'}), 400
            
        try:
            from sqlalchemy import text
            db.session.execute(text("CREATE TABLE IF NOT EXISTS blocked_ips (id INT AUTO_INCREMENT PRIMARY KEY, ip_address VARCHAR(45) UNIQUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"))
            db.session.execute(text("INSERT IGNORE INTO blocked_ips (ip_address) VALUES (:ip_address)"), {'ip_address': ip_address})
            
            # Terminate all active sessions from this IP
            db.session.execute(text("UPDATE user_sessions SET logout_time = NOW() WHERE ip_address = :ip_address AND logout_time IS NULL"), {'ip_address': ip_address})
            
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500
            
    return jsonify({'success': False, 'message': 'Invalid request format'}), 400


@admin_bp.route('/users')
@admin_login_required
def admin_users():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    search = request.args.get('search', '').strip()  # type: ignore
    status_filter = request.args.get('status', 'all')
    sort_by = request.args.get('sort', 'created_at')
    order = request.args.get('order', 'desc')

    allowed_sort_cols = ['id', 'username', 'email', 'created_at', 'is_active', 'mobile_number']
    if sort_by not in allowed_sort_cols:
        sort_by = 'created_at'
    if order not in ['asc', 'desc']:
        order = 'desc'

    try:
        from sqlalchemy import text
        query = """
            SELECT id, username, email, created_at,
                   COALESCE(is_active, 1) as is_active,
                   mobile_number, expiry_date
            FROM users
            WHERE 1=1
        """
        params = {}

        if search:
            query += " AND (username LIKE :s1 OR email LIKE :s2 OR mobile_number LIKE :s3)"
            params.update({'s1': f"%{search}%", 's2': f"%{search}%", 's3': f"%{search}%"})  # type: ignore

        if status_filter == 'active':
            query += " AND (is_active = 1 OR is_active IS NULL)"
        elif status_filter == 'inactive':
            query += " AND is_active = 0"

        count_query = f"SELECT COUNT(*) as total FROM ({query}) as subquery"
        total_res = db.session.execute(text(count_query), params).fetchone()
        total = total_res.total if total_res else 0

        query += f" ORDER BY {sort_by} {order} LIMIT :limit OFFSET :offset"
        params.update({'limit': per_page, 'offset': (page - 1) * per_page})  # type: ignore

        users_res = db.session.execute(text(query), params).fetchall()
        users = [dict(row._mapping) for row in users_res]

        pagination = {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': (total + per_page - 1) // per_page
        }

        return render_template('admin/users.html',
                             users=users,
                             pagination=pagination,
                             search=search,
                             status=status_filter,
                             sort=sort_by,
                             order=order)
    except Exception as err:
        print(f"Database error: {err}")
        flash('Error retrieving users', 'danger')
        return render_template('admin/users.html',
                         users=[],
                         pagination={},
                         search=search)

@admin_bp.route('/users/<int:user_id>/edit')
@admin_login_required
def admin_edit_user(user_id):
    try:
        from sqlalchemy import text
        query = text("""
            SELECT id, username, email, created_at,
                   COALESCE(is_active, 1) as is_active,
                   mobile_number, expiry_date, updated_at
            FROM users
            WHERE id = :user_id
        """)
        user = db.session.execute(query, {'user_id': user_id}).fetchone()

        if not user:
            return jsonify({'success': False, 'message': 'User not found'}), 404

        user_dict = dict(user._mapping)
        if user_dict['expiry_date']:
            user_dict['expiry_date'] = user_dict['expiry_date'].strftime('%Y-%m-%d')

        return jsonify(user_dict)
    except Exception as err:
        print(f"Database error: {err}")
        return jsonify({'success': False, 'message': 'Error retrieving user data'}), 500

@admin_bp.route('/admin-users')
@admin_login_required
def admin_users_list():
    """List all admin users with pagination and search"""
    if session.get('admin_role') != 'admin':
        flash('You do not have permission to access this page', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))

    page = request.args.get('page', 1, type=int)
    per_page = 10
    search = request.args.get('search', '').strip()  # type: ignore
    role_filter = request.args.get('role', 'all')
    status_filter = request.args.get('status', 'all')

    try:
        from sqlalchemy import text
        query = """
            SELECT id, username, email, role, is_active,
                   created_at, last_login, mobile_number, expiry_date
            FROM admin_users
            WHERE 1=1
        """
        params = {}

        if search:
            query += " AND (username LIKE :s1 OR email LIKE :s2 OR mobile_number LIKE :s3)"
            params.update({'s1': f"%{search}%", 's2': f"%{search}%", 's3': f"%{search}%"})  # type: ignore

        if role_filter != 'all':
            query += " AND role = :role"
            params.update({'role': role_filter})  # type: ignore

        if status_filter != 'all':
            query += " AND is_active = :status"
            params.update({'status': status_filter == 'active'})  # type: ignore

        count_query = f"SELECT COUNT(*) as total FROM ({query}) as subquery"
        total_res = db.session.execute(text(count_query), params).fetchone()
        total = total_res.total if total_res else 0

        query += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        params.update({'limit': per_page, 'offset': (page - 1) * per_page})  # type: ignore

        users_res = db.session.execute(text(query), params).fetchall()
        users = [dict(row._mapping) for row in users_res]

        pagination = {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': (total + per_page - 1) // per_page
        }

        return render_template('admin/admin_users/list.html',
                             users=users,
                             pagination=pagination,
                             search=search,
                             role_filter=role_filter,
                             status_filter=status_filter)
    except Exception as err:
        print(f"Database error: {err}")
        flash('Error retrieving admin users', 'danger')
        return render_template('admin/admin_users/list.html',
                           users=[],
                           pagination={},
                           search=search,
                           role_filter=role_filter,
                           status_filter=status_filter)

@admin_bp.route('/admin-users/add', methods=['GET', 'POST'])
@admin_login_required
def admin_users_add():
    """Add a new admin user"""
    if session.get('admin_role') != 'admin':
        flash('You do not have permission to access this page', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))

    form = AdminUserForm(request.form)
    form.is_edit = False  # type: ignore

    if request.method == 'POST' and form.validate():
        username = form.username.data.strip()  # type: ignore
        email = form.email.data.strip().lower()  # type: ignore
        password = form.password.data
        role = form.role.data
        is_active = form.is_active.data == '1'
        mobile_number = form.mobile_number.data.strip() if form.mobile_number.data else None  # type: ignore
        expiry_date = form.expiry_date.data

        try:
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')  # type: ignore
            try:
                new_admin = AdminUsers(
                    username=username,
                    email=email,
                    password=hashed_password,
                    role=role,
                    is_active=is_active,
                    mobile_number=mobile_number,
                    expiry_date=expiry_date
                )
                db.session.add(new_admin)
                db.session.commit()
                flash('Admin user added successfully', 'success')
                return redirect(url_for('admin_bp.admin_users_list'))
            except Exception as err:
                db.session.rollback()
                print(f"Database error: {err}")
                flash('Error adding admin user (maybe duplicate username or email)', 'danger')
        except ValueError:
            flash('Password must be non-empty', 'danger')

    return render_template('admin/admin_users/add_edit.html', form=form, is_edit=False)

@admin_bp.route('/admin-users/edit/<int:user_id>', methods=['GET', 'POST'])
@admin_login_required
def admin_users_edit(user_id):
    """Edit an existing admin user"""
    if session.get('admin_role') != 'admin':
        flash('You do not have permission to access this page', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))

    try:
        
        user_obj = db.session.scalars(db.select(AdminUsers).filter_by(id=user_id)).first()

        if not user_obj:
            flash('Admin user not found', 'danger')
            return redirect(url_for('admin_bp.admin_users_list'))

        user = {
            'id': user_obj.id, 'username': user_obj.username, 'email': user_obj.email,
            'role': user_obj.role, 'is_active': user_obj.is_active,
            'mobile_number': user_obj.mobile_number, 'expiry_date': user_obj.expiry_date
        }

        form = AdminUserForm(request.form, data=user)
        form.is_edit = True  # type: ignore
        form.is_active.data = '1' if user['is_active'] else '0'

        if request.method == 'POST' and form.validate():
            username = form.username.data.strip()  # type: ignore
            email = form.email.data.strip().lower()  # type: ignore
            password = form.password.data
            role = form.role.data
            is_active = form.is_active.data == '1'
            mobile_number = form.mobile_number.data.strip() if form.mobile_number.data else None  # type: ignore
            expiry_date = form.expiry_date.data

            update_password = bool(password)

            try:
                user_obj.username = username
                user_obj.email = email
                user_obj.role = role
                user_obj.is_active = is_active
                user_obj.mobile_number = mobile_number
                user_obj.expiry_date = expiry_date
                user_obj.updated_at = db.func.now()
                
                if update_password:
                    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')  # type: ignore
                    user_obj.password = hashed_password

                db.session.commit()
                flash('Admin user updated successfully', 'success')
                return redirect(url_for('admin_bp.admin_users_list'))
            except Exception as err:
                db.session.rollback()
                print(f"Database error: {err}")
                flash('Error updating admin user', 'danger')

        return render_template('admin/admin_users/add_edit.html',
                             form=form,
                             is_edit=True,
                             user=user)
    except Exception as err:
        print(f"Database error: {err}")
        flash('Error retrieving admin user', 'danger')
        return redirect(url_for('admin_bp.admin_users_list'))

@admin_bp.route('/admin-users/delete/<int:user_id>', methods=['POST'])
@admin_login_required
def admin_users_delete(user_id):
    """Delete an admin user (soft delete)"""
    if session.get('admin_role') != 'admin':
        flash('You do not have permission to perform this action', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))

    if user_id == session.get('admin_id'):
        flash('You cannot delete your own account', 'danger')
        return redirect(url_for('admin_bp.admin_users_list'))

    try:
        user_obj = db.session.scalars(db.select(AdminUsers).filter_by(id=user_id)).first()
        if user_obj:
            user_obj.is_active = False
            db.session.commit()
            flash('Admin user deactivated successfully', 'success')
        else:
            flash('Admin user not found', 'danger')
    except Exception as err:
        db.session.rollback()
        print(f"Database error: {err}")
        flash('Error deactivating admin user', 'danger')

    return redirect(url_for('admin_bp.admin_users_list'))

@admin_bp.route('/admin-users/toggle-status/<int:user_id>', methods=['POST'])
@admin_login_required
def admin_users_toggle_status(user_id):
    """Toggle admin user active status"""
    if session.get('admin_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission denied'}), 403

    if user_id == session.get('admin_id'):
        return jsonify({'success': False, 'message': 'You cannot change your own status'}), 400

    action = request.form.get('action')
    if action not in ['activate', 'deactivate']:
        return jsonify({'success': False, 'message': 'Invalid action'}), 400

    new_status = action == 'activate'

    try:
        user_obj = db.session.scalars(db.select(AdminUsers).filter_by(id=user_id)).first()
        if user_obj:
            user_obj.is_active = new_status
            user_obj.updated_at = db.func.now()
            db.session.commit()
            return jsonify({'success': True, 'message': f'User {action}d successfully'})
        return jsonify({'success': False, 'message': 'User not found'}), 404
    except Exception as err:
        db.session.rollback()
        print(f"Database error: {err}")
        return jsonify({'success': False, 'message': 'Error updating user status'}), 500

@admin_bp.route('/users/update', methods=['POST'])
@admin_login_required
def admin_update_user():
    try:
        user_id = request.form['user_id']
        username = request.form['username']
        email = request.form['email']
        mobile_number = request.form.get('mobile_number', '').strip()  # type: ignore
        expiry_date = request.form.get('expiry_date')
        expiry_date = expiry_date if expiry_date else None
        is_active = 1 if request.form.get('is_active', '0') == '1' else 0
    except KeyError:
        return jsonify({'success': False, 'message': 'Missing required fields'}), 400

    try:
        user_obj = db.session.scalars(db.select(Users).filter_by(id=user_id)).first()
        if user_obj:
            user_obj.username = username
            user_obj.email = email
            user_obj.is_active = is_active
            user_obj.mobile_number = mobile_number if mobile_number else None
            user_obj.expiry_date = expiry_date
            user_obj.updated_at = db.func.now()
            db.session.commit()
            return jsonify({'success': True, 'message': 'User updated successfully'})
        return jsonify({'success': False, 'message': 'User not found'}), 404
    except Exception as err:
        db.session.rollback()
        print(f"Database error: {err}")
        return jsonify({'success': False, 'message': 'Error updating user (possible duplicate)'}), 500

@admin_bp.route('/users/reset-password', methods=['POST'])
@admin_login_required
def admin_reset_password():
    user_id = request.form.get('user_id')
    new_password = request.form.get('new_password', '').strip()  # type: ignore
    confirm_password = request.form.get('confirm_password', '').strip()  # type: ignore
    if not user_id or not new_password or not confirm_password:
        return jsonify({'success': False, 'message': 'Missing required fields'}), 400

    if new_password != confirm_password:
        return jsonify({'success': False, 'message': 'Passwords do not match'}), 400

    if len(new_password) < 8:
        return jsonify({'success': False, 'message': 'Password must be at least 8 characters'}), 400

    hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')  # type: ignore

    try:
        user_obj = db.session.scalars(db.select(Users).filter_by(id=user_id)).first()
        if user_obj:
            user_obj.password = hashed_password
            db.session.commit()
            return jsonify({'success': True, 'message': 'Password reset successfully'})
        return jsonify({'success': False, 'message': 'User not found'}), 404
    except Exception as err:
        db.session.rollback()
        print(f"Database error: {err}")
        return jsonify({'success': False, 'message': 'Error resetting password'}), 500

@admin_bp.route('/users/<int:user_id>/toggle-status', methods=['POST'])
@admin_login_required
def admin_toggle_user_status(user_id):
    action = request.form.get('action', '').strip().lower()  # type: ignore

    if action not in ['activate', 'deactivate']:
        return jsonify({'success': False, 'message': 'Invalid action'}), 400

    new_status = action == 'activate'

    try:
        user_obj = db.session.scalars(db.select(Users).filter_by(id=user_id)).first()
        if user_obj:
            user_obj.is_active = new_status
            db.session.commit()
            return jsonify({'success': True, 'message': f'User {action}d successfully'})
        return jsonify({'success': False, 'message': 'User not found'}), 404
    except Exception as err:
        db.session.rollback()
        print(f"Database error: {err}")
        return jsonify({'success': False, 'message': f'Error {action}ing user'}), 500

@admin_bp.route('/users/<int:user_id>/orders')
@admin_login_required
def admin_user_orders(user_id):
    page = request.args.get('page', 1, type=int)
    per_page = 10

    try:
        from sqlalchemy import text
        
        user_obj = db.session.scalars(db.select(Users).filter_by(id=user_id)).first()
        if not user_obj:
            flash('User not found', 'danger')
            return redirect(url_for('admin_bp.admin_users'))

        # Get orders count
        total_res = db.session.execute(text("SELECT COUNT(*) as total FROM orders WHERE user_id = :user_id"), {'user_id': user_id}).fetchone()
        total = total_res.total if total_res else 0

        # Get orders
        query = text("""
            SELECT o.id, o.order_date, o.total_amount, o.status,
                   COUNT(oi.id) as item_count
            FROM orders o
            LEFT JOIN order_items oi ON o.id = oi.order_id
            WHERE o.user_id = :user_id
            GROUP BY o.id
            ORDER BY o.order_date DESC
            LIMIT :limit OFFSET :offset
        """)
        orders_res = db.session.execute(query, {'user_id': user_id, 'limit': per_page, 'offset': (page - 1) * per_page}).fetchall()
        orders = [dict(row._mapping) for row in orders_res]

        pagination = {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': (total + per_page - 1) // per_page
        }

        return render_template('admin/user_orders.html',
                             orders=orders,
                             pagination=pagination,
                             user_id=user_id,
                             username=user_obj.username)
    except Exception as err:
        print(f"Database error: {err}")
        flash('Error retrieving user orders', 'danger')
        return render_template('admin/user_orders.html', orders=[], pagination={}, user_id=user_id)
